import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from scimirror.backend import Backend, validate_response
from scimirror.common import digest, dump
from scimirror.corpus import Corpus
from scimirror.engine import observation, step
from scimirror.events import Journal, replay
from scimirror.experiment import ROOT, load_config, run, estimate
from scimirror.state import initialize, validate_world
from scimirror.metrics import paired_effects


class CoreTests(unittest.TestCase):
    # 为每个测试准备默认 mock 配置、合成语料和后端实例。
    def setUp(self):
        self.cfg = load_config(ROOT/'configs/mock.json')
        self.corpus = Corpus(ROOT/self.cfg['corpus'],2025,True)
        self.backend = Backend(self.cfg,ROOT)

    # 验证二十个 Agent 的确定性、团队容量和提案修订行为。
    def test_twenty_agents_deterministic_and_capacity(self):
        a, b = initialize(20,42), initialize(20,42)
        for _ in range(30):
            a,_ = step(a,self.corpus,self.backend,self.cfg)
            b,_ = step(b,self.corpus,self.backend,self.cfg)
            validate_world(a)
            self.assertEqual(digest(a.export()),digest(b.export()))
        self.assertEqual(len(a.agents),20)
        self.assertTrue(any(len(t['members'])==2 for t in a.teams.values()))
        self.assertTrue(any(len(x['versions'])==2 for x in a.ideas.values()))

    # 验证观察上下文不会泄露其他 Agent 的私有值和记忆。
    def test_observation_no_other_private_state(self):
        w = initialize(20,42)
        w.agents['s01'].memories=[{'secret':'PRIVATE_SENTINEL'}]
        w.agents['s01'].values['novelty']=0.123456789
        ctx = observation(w,'s00',self.corpus)
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(ctx))
        self.assertNotIn('0.123456789',json.dumps(ctx))
        self.assertTrue(all('values' not in p for p in ctx['peers']))

    # 验证未来文献过滤及禁止合成语料的配置约束。
    def test_time_boundary_and_synthetic_guard(self):
        with tempfile.TemporaryDirectory() as d:
            records=list(self.corpus.papers.values())[:3]
            records.append({**records[0], 'id':'future', 'year':2025})
            path=Path(d)/'papers.jsonl'
            path.write_text('\n'.join(json.dumps(p) for p in records))
            self.assertNotIn('future',Corpus(path,2025,True).papers)
            with self.assertRaises(ValueError):
                Corpus(path,2025,False)

    # 验证模型生成不可见或虚构引用时会被响应校验拒绝。
    def test_unknown_reference_rejected(self):
        ctx=observation(initialize(20,42),'s00',self.corpus)
        response=self.backend.generate('propose',ctx,[42,0,'s00'])
        response['candidates'][0]['references']=['not_visible']
        with self.assertRaises(ValueError):
            validate_response('propose',response,ctx)

    # 验证生成失败不会部分修改输入世界状态。
    def test_failed_step_no_state_mutation(self):
        w=initialize(20,42)
        initial=digest(w.export())
        with patch.object(self.backend,'generate',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                step(w,self.corpus,self.backend,self.cfg)
        self.assertEqual(initial,digest(w.export()))

    # 验证日志可恢复原状态且篡改事件会触发完整性错误。
    def test_replay_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'events.jsonl'
            journal=Journal(path,'test')
            w=initialize(20,42)
            journal.commit(w,[])
            for _ in range(6):
                w,events=step(w,self.corpus,self.backend,self.cfg)
                journal.commit(w,events)
            self.assertEqual(digest(w.export()),digest(replay(path).export()))
            lines=path.read_text().splitlines()
            bad=json.loads(lines[0]);bad['tick']=999
            lines[0]=json.dumps(bad)
            path.write_text('\n'.join(lines)+'\n')
            with self.assertRaises(ValueError):
                replay(path)

    # 验证 closed 合作网络中的团队成员始终来自同一领域。
    def test_closed_network_never_crosses_fields(self):
        w=initialize(20,42);w.network='closed'
        for _ in range(12):
            w,_=step(w,self.corpus,self.backend,self.cfg)
            for team in w.teams.values():
                self.assertEqual(len({w.agents[x].field for x in team['members']}),1)

    # 验证六个分支共享前缀状态且盲评导出不泄露条件标签。
    def test_branch_run_same_prefix_and_blind_export(self):
        cfg={**self.cfg,'ticks':12,'seeds':[42]}
        with tempfile.TemporaryDirectory() as d:
            out=run(cfg,Path(d)/'result')
            fork=json.loads((out/'seed_42/prefix/fork.json').read_text())
            for f in out.glob('seed_42/*/events.jsonl'):
                if f.parent.name=='prefix':continue
                first=json.loads(f.read_text().splitlines()[0])
                self.assertEqual(first['payload']['fork_state_hash'],fork['state_hash'])
                raw=json.loads(f.read_text().splitlines()[1])['payload']
                raw['policy']='balanced';raw['network']='open'
                self.assertEqual(digest(raw),fork['state_hash'])
            header=(out/'blind_review.csv').read_text(encoding='utf-8-sig').splitlines()[0]
            self.assertNotIn('policy',header)
            self.assertNotIn('reward',header)
            self.assertEqual(estimate(cfg)['logical_calls_without_cache_or_retries'],210)

    # 验证处理组与对照组完全相同时配对效应及区间均为零。
    def test_paired_bootstrap_zero_effect(self):
        rows=[]
        for s in range(3):
            for p in ['balanced','novelty','recognition']:
                rows.append({'seed':s,'network':'open','policy':p,'novelty_proxy':s,'text_diversity':s,
                             'cross_field_rate':s,'mean_team_size':s,'outputs':s})
        for row in paired_effects(rows):
            self.assertEqual(row['mean_difference'],0)
            self.assertEqual(row['ci95_low'],0)
            self.assertEqual(row['ci95_high'],0)

    # 验证兼容 HTTP 后端、用量统计、缓存命中及密钥不落盘。
    def test_chat_http_and_cache(self):
        ctx=observation(initialize(20,42),'s00',self.corpus)
        response=self.backend.generate('propose',ctx,[42,0,'s00'])
        class Handler(BaseHTTPRequestHandler):
            # 接收本地测试请求并返回符合 Chat Completions 结构的响应。
            def do_POST(self):
                self.server.count+=1
                req=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.request=req
                body=json.dumps({'choices':[{'message':{'content':json.dumps(response)}}],
                                 'usage':{'prompt_tokens':10,'completion_tokens':20}}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(body)
            # 禁用本地测试 HTTP 服务器的默认访问日志输出。
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Handler);server.count=0
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{
                'SCIMIRROR_BASE_URL':f'http://127.0.0.1:{server.server_port}/v1',
                'SCIMIRROR_MODEL':'test-model','SCIMIRROR_API_KEY':'local-test'}):
                b=Backend({**self.cfg,'backend':'chat'},d)
                self.assertEqual(b.generate('propose',ctx,[42,0,'s00']),response)
                self.assertEqual(b.generate('propose',ctx,[42,0,'s00']),response)
                self.assertEqual(server.count,1)
                self.assertEqual(b.hits,1)
                self.assertEqual(b.prompt_tokens,10)
                self.assertNotIn('local-test',next(Path(d).glob('cache/*.json')).read_text())
        finally:
            server.shutdown();server.server_close();thread.join()

    # 验证从项目目录之外调用 CLI 时仍能正确解析项目相对路径。
    def test_cli_paths_work_outside_project_directory(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run(
                [sys.executable, str(ROOT/'run.py'), 'doctor', '--config', 'configs/mock.json'],
                cwd=d, check=True, capture_output=True, text=True
            )
        self.assertIn('Backend: mock', result.stdout)
        self.assertIn('Corpus papers after validation/filtering: 90', result.stdout)
        self.assertIn(str(Path(sys.executable).resolve()), result.stdout)

    # 验证原子 JSON 写入会重试瞬时目标文件锁并最终成功。
    def test_atomic_dump_retries_transient_destination_lock(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'checkpoint.json'
            real_replace = os.replace
            attempts = []

            # 在首次替换时模拟扫描器锁定目标文件，随后执行真实替换。
            def transient_lock(source, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError('transient scanner lock')
                return real_replace(source, destination)

            with patch('scimirror.common.os.replace', side_effect=transient_lock), \
                 patch('scimirror.common.time.sleep'):
                dump(target, {'status': 'complete'})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'status': 'complete'})
            self.assertEqual(len(attempts), 2)


if __name__=='__main__':unittest.main()
