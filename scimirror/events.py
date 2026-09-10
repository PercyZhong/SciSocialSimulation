import json
from pathlib import Path
from .common import canonical, digest, dump
from .state import World, validate_world


class Journal:
    """Append-only tick transactions; final world.snapshot is the commit record.

    Replay uses recorded snapshots, never regenerates model outputs. Domain events
    support audit; this MVP does not claim a per-field reducer for every event type.
    """
    # 创建指定分支的只追加事件日志并拒绝覆盖已有日志。
    def __init__(self, path, branch):
        self.path = Path(path)
        self.branch = branch
        self.seq = 0
        self.prev = '0' * 64
        if self.path.exists():
            raise FileExistsError('Refuse to overwrite event log')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # 将领域事件和世界快照作为一个 tick 事务写入哈希链并更新检查点。
    def commit(self, world, pending):
        validate_world(world)
        records = list(pending) + [{'type': 'world.snapshot', 'actor': 'system',
                                    'audience': [], 'payload': world.export()}]
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            for r in records:
                self.seq += 1
                e = {'schema_version': 1, 'event_id': f'{self.branch}:{self.seq}',
                     'branch': self.branch, 'tick': world.tick, 'seq': self.seq,
                     'previous_hash': self.prev, **r}
                e['hash'] = digest(e)
                stream.write(canonical(e)+'\n')
                self.prev = e['hash']
            stream.flush()
        dump(self.path.parent / 'checkpoint.json', {'state': world.export(), 'journal_hash': self.prev})


# 校验事件日志哈希链并从最后一个已提交快照恢复世界状态。
def replay(path):
    previous = '0' * 64
    last = None
    seq = 0
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        stored = event.pop('hash')
        seq += 1
        if digest(event) != stored or event['previous_hash'] != previous or event['seq'] != seq:
            raise ValueError('Journal integrity check failed')
        previous = stored
        if event['type'] == 'world.snapshot':
            last = event['payload']
    if last is None:
        raise ValueError('No committed snapshot')
    world = World.restore(last)
    validate_world(world)
    return world


class V02Journal:
    """Append-only v0.2 transactions with the same hash-chain protocol."""
    # 创建v0.2分支事件日志且拒绝覆盖旧日志。
    def __init__(self, path, branch):
        self.path = Path(path)
        self.branch = branch
        self.seq = 0
        self.prev = '0' * 64
        if self.path.exists():
            raise FileExistsError('Refuse to overwrite event log')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # 校验v0.2世界并提交领域事件、快照和检查点。
    def commit(self, world, pending):
        from .v02_state import validate_v02
        validate_v02(world)
        records = list(pending) + [{'type': 'world.snapshot.v02', 'actor': 'system',
                                    'audience': [], 'payload': world.export()}]
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            for record in records:
                self.seq += 1
                value = {'schema_version': '0.2', 'event_id': f'{self.branch}:{self.seq}',
                         'branch': self.branch, 'tick': world.tick, 'seq': self.seq,
                         'previous_hash': self.prev, **record}
                value['hash'] = digest(value)
                stream.write(canonical(value)+'\n')
                self.prev = value['hash']
            stream.flush()
        dump(self.path.parent/'checkpoint.json', {'schema_version': '0.2', 'state': world.export(),
                                                  'journal_hash': self.prev})


# 校验v0.2日志哈希链并从最后一个完整快照恢复世界。
def replay_v02(path):
    from .v02_state import WorldV02, validate_v02
    previous, last, seq = '0'*64, None, 0
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        stored = record.pop('hash')
        seq += 1
        if digest(record) != stored or record['previous_hash'] != previous or record['seq'] != seq:
            raise ValueError('V0.2 journal integrity check failed')
        previous = stored
        if record['type'] == 'world.snapshot.v02':
            last = record['payload']
    if last is None:
        raise ValueError('No committed v0.2 snapshot')
    world = WorldV02.restore(last)
    validate_v02(world)
    return world
