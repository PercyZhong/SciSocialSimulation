"""Stage C delivery assembly and extracted-ZIP replay validation."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from .stage_c_packaging import sha, write_manifest, verify_manifest


def replay_validate(run_dir):
    run_dir=Path(run_dir)
    with (run_dir/"DECISIONS.csv").open(encoding="utf-8-sig",newline="") as handle:
        rows=list(csv.DictReader(handle))
    with (run_dir/"REQUEST_SCHEDULE.csv").open(encoding="utf-8-sig",newline="") as handle:
        schedule=list(csv.DictReader(handle))
    caches=list((run_dir/"cache").rglob("*.json")); valid=sum(r.get("status")=="validated" for r in rows)
    expected=len(schedule)
    return {"status":"passed" if len(rows)==expected and valid==expected and len(caches)==expected else "failed",
            "expected_rows":expected,"decision_rows":len(rows),"validated_rows":valid,
            "cache_records":len(caches),"offline":True}


def finalize(run_dir,root):
    run_dir,root=Path(run_dir),Path(root); source=run_dir/"reproduction"/"source"
    if source.exists(): shutil.rmtree(source)
    (source/"scimirror").mkdir(parents=True)
    for name in ("model_registry.py","decision_schema.py","decision_backend.py","usage_ledger.py","stage_c_frozen.py","stage_c_live.py","stage_c_delivery.py","stage_c_packaging.py","common.py","__init__.py"):
        shutil.copy2(root/"scimirror"/name,source/"scimirror"/name)
    shutil.copy2(root/"execute_stage_c.py",source/"execute_stage_c.py")
    shutil.copy2(root/"STAGE_C_CHANGELOG.md",source/"STAGE_C_CHANGELOG.md")
    (source/"configs").mkdir()
    for name in ("stage_c_mock.json","stage_c_live.example.json","stage_c_pilot.example.json","models.stage_c.example.json"):
        shutil.copy2(root/"configs"/name,source/"configs"/name)
    (run_dir/"reproduction"/"README_REPRODUCE.md").write_text(
        "# Offline replay\n\n`python execute_stage_c.py replay --run-dir <extracted-delivery> --offline`\n",encoding="utf-8")
    archive=run_dir/"stage_c_offline_delivery.zip"; checksum=run_dir/"CHECKSUMS.sha256"
    for path in (archive,checksum):
        if path.exists(): path.unlink()
    write_manifest(run_dir)
    _zip(run_dir,archive)
    first=_extracted_replay(archive)
    (run_dir/"ZIP_REPRODUCTION_VALIDATION.json").write_text(json.dumps(first,indent=2)+"\n",encoding="utf-8")
    checksum.unlink(); archive.unlink(); write_manifest(run_dir); _zip(run_dir,archive)
    final=_extracted_replay(archive)
    return {"archive":str(archive.resolve()),"sha256":sha(archive),"files":sum(1 for p in run_dir.rglob("*") if p.is_file()),"zip_reproduction":final}


def _zip(root,archive):
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for path in sorted(p for p in root.rglob("*") if p.is_file() and p!=archive):
            bundle.write(path,Path("delivery")/path.relative_to(root))


def _extracted_replay(archive):
    with tempfile.TemporaryDirectory(prefix="scimirror_stage_c_zip_") as temp:
        with zipfile.ZipFile(archive) as bundle: bundle.extractall(temp)
        delivery=Path(temp)/"delivery"; source=delivery/"reproduction"/"source"
        env=os.environ.copy(); env["PYTHONPATH"]=str(source)
        command=[sys.executable,str(source/"execute_stage_c.py"),"replay","--run-dir",str(delivery),"--offline"]
        completed=subprocess.run(command,cwd=source,env=env,text=True,capture_output=True)
        probe=subprocess.run([sys.executable,"-c","import scimirror.stage_c_frozen as m; print(m.__file__)"],cwd=source,env=env,text=True,capture_output=True)
        loaded=probe.stdout.strip(); inside=probe.returncode==0 and Path(loaded).resolve().is_relative_to(source.resolve())
        manifest=verify_manifest(delivery)
        return {"status":"passed" if completed.returncode==0 and inside and manifest["status"]=="passed" else "failed",
                "return_code":completed.returncode,"module_path":loaded,"module_inside_extracted_tree":inside,
                "checksum_status":manifest["status"],"stderr":completed.stderr+probe.stderr}
