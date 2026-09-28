"""Repair Stage B nested manifests and create independently reproducible archives."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_manifest(root):
    path=Path(root)/"CHECKSUMS.sha256"; failures=[]; checked=0
    if not path.exists(): return {"status":"missing","checked":0,"failures":["CHECKSUMS.sha256"]}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        expected,rel=line.split(None,1); target=Path(root)/rel.strip().removeprefix("./"); checked+=1
        if not target.is_file() or sha(target)!=expected: failures.append(rel.strip())
    return {"status":"passed" if not failures else "failed","checked":checked,"failures":failures}


def write_manifest(root):
    root=Path(root); path=root/"CHECKSUMS.sha256"
    files=sorted(p for p in root.rglob("*") if p.is_file() and p!=path)
    path.write_text("".join(f"{sha(p)}  {p.relative_to(root).as_posix()}\n" for p in files),encoding="utf-8")
    return len(files)


def _safe_extract(archive,destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            target=(destination/item.filename).resolve()
            if destination not in target.parents and target!=destination: raise ValueError("Unsafe archive path")
        bundle.extractall(destination)


def repair_stage_b_archive(archive, output_archive, kind):
    archive=Path(archive).resolve(); output_archive=Path(output_archive).resolve()
    with tempfile.TemporaryDirectory(prefix="scimirror_stage_b_repair_") as temp:
        temp=Path(temp); _safe_extract(archive,temp); root=temp/"delivery"
        original=verify_manifest(root)
        if original["status"]!="passed": raise ValueError("Parent archive checksum validation failed")
        reconstructed=[]
        candidates=[]
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
            if (directory/"FROZEN_PILOT.json").exists() or (directory/"PLAN_FROZEN.json").exists(): candidates.append(directory)
        for directory in candidates:
            if not (directory/"CHECKSUMS.sha256").exists():
                count=write_manifest(directory); reconstructed.append({"path":directory.relative_to(root).as_posix()+"/CHECKSUMS.sha256","files":count,"origin":"reconstructed_manifest"})
        provenance={"schema_version":"stage_b_archive_repair_1","kind":kind,"parent_archive_sha256":sha(archive),
                    "parent_manifest_validation":original,"reconstructed_manifests":reconstructed,"historical_zip_modified":False,
                    "source_repairs":["numeric metric comparison uses 1e-12 tolerance; rankings and nonnumeric fields remain exact"]}
        module_name="stage_b_candidate_diagnostic.py" if kind=="candidate" else "stage_b_improvement.py"
        current_module=Path(__file__).with_name(module_name)
        target_module=root/"reproduction"/"source"/"scimirror"/module_name
        if target_module.exists(): shutil.copy2(current_module,target_module)
        if kind=="candidate" and (root/"REPORT_ZH.md").exists():
            report=(root/"REPORT_ZH.md").read_text(encoding="utf-8")
            status=json.loads((root/"STATUS.json").read_text(encoding="utf-8"))
            if status.get("linux_authoritative_validation")=="passed":
                start=report.find("## Linux验收")
                end=report.find("## 决策",start)
                if start>=0 and end>start:
                    report=report[:start]+"## Linux验收\n\nlinux_authoritative_validation=passed；见 TEST_STATUS.json 与 MOCK_REPLAY_VALIDATION.json。\n\n"+report[end:]
                duplicate=report.find("\n## Linux 权威验收\n",end)
                if duplicate>=0: report=report[:duplicate].rstrip()+"\n"
                (root/"REPORT_ZH.md").write_text(report,encoding="utf-8")
        (root/"ARCHIVE_REPAIR_PROVENANCE.json").write_text(json.dumps(provenance,indent=2)+"\n",encoding="utf-8")
        write_manifest(root)
        output_archive.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output_archive,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
            for path in sorted(p for p in root.rglob("*") if p.is_file()): bundle.write(path,Path("delivery")/path.relative_to(root))
    result=validate_repaired_zip(output_archive,kind)
    return {"archive":str(output_archive),"sha256":sha(output_archive),"parent_archive_sha256":sha(archive),"reconstructed_manifests":reconstructed,"zip_reproduction":result}


def validate_repaired_zip(archive,kind):
    with tempfile.TemporaryDirectory(prefix="scimirror_zip_repro_") as temp:
        temp=Path(temp); _safe_extract(archive,temp); root=temp/"delivery"; manifests=[]
        for manifest in root.rglob("CHECKSUMS.sha256"):
            result=verify_manifest(manifest.parent); manifests.append({"path":manifest.relative_to(root).as_posix(),**result})
        source=root/"reproduction"/"source"
        command=[sys.executable,str(source/("execute_stage_b_candidate_diagnostic.py" if kind=="candidate" else "execute_stage_b_improvement.py")),
                 "reproduce","--run-dir",str(root),"--output",str(temp/"reproduced")]
        env=os.environ.copy(); env["PYTHONPATH"]=str(source)
        completed=subprocess.run(command,cwd=source,env=env,text=True,capture_output=True)
        probe=subprocess.run([sys.executable,"-c",f"import scimirror.stage_b_{'candidate_diagnostic' if kind=='candidate' else 'improvement'} as m; print(m.__file__)"],cwd=source,env=env,text=True,capture_output=True)
        loaded=probe.stdout.strip(); inside=probe.returncode==0 and Path(loaded).resolve().is_relative_to(source.resolve())
        return {"status":"passed" if completed.returncode==0 and inside and all(m["status"]=="passed" for m in manifests) else "failed",
                "return_code":completed.returncode,"module_path":loaded,"module_inside_extracted_tree":inside,
                "manifests":manifests,"stdout":completed.stdout,"stderr":completed.stderr+probe.stderr}
