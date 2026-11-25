"""大世界掉落入库的实例隔离回归。

背景：2026-11-21 的实例隔离改动（9884e7a56）把
``AzurStats._parse_local_opsi_items`` 的形参从
``(image, imgid, genre, combat_count, filename=None)`` 改成了带 ``self`` 的版本，
但装饰器仍是 ``@staticmethod``；同时 INSERT 语句没有带上新增的 ``instance`` 列。

后果两连：

1. ``_record_local`` 的调用把 ``image`` 顶到 ``self`` 位置，抛
   ``TypeError: missing 1 required positional argument: 'combat_count'``，
   被 ``except`` 吞成一行 WARNING —— 2026-11-22 起大世界掉落一条都没入库，
   日志每天 16~33 次「本地碧蓝统计解析失败」、0 次成功。
2. 即使签名对上，写进去的行 ``instance`` 也是 NULL；而
   ``_ensure_local_db`` 每次建表都跑 ``UPDATE ... WHERE instance IS NULL``，
   会把它搬回实例列表第一个（主号 alas）名下 —— 小号的掉落记到主号头上。

这里锁两件事：写入能走通、写进去的行带真实实例名；以及老库迁移的 UPDATE
只在首次加列时执行，不会持续吞掉后来的 NULL 行。
"""

import gc
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.azur_stats.scene.operation_siren import DataOpsiItems
from module.statistics.azurstats import AzurStats


class _FakeScene:
    """解析器替身：不跑 OCR，固定吐一条侵蚀 5 的掉落。

    ``_parse_local_opsi_items`` 只用到 ``load_file`` / ``parse_scene`` 两件事，
    以及塞进 ``__dict__`` 的 ``imgid``（``_save_unknown_item_images`` 才读，
    本用例物品名不是纯数字，不会走到那里）。
    """

    def __init__(self):
        self.__dict__['imgid'] = ''

    def load_file(self, image):
        """假装载入了一组截图。"""

    def parse_scene(self):
        """固定产出一条掉落行。"""
        yield DataOpsiItems(
            imgid='', server='cn', zone='5-1', zone_type='SAFE', zone_id=51,
            hazard_level=5, item='PlateGeneralT4', amount=1, tag='scan',
        )


class _OpsiStatsTestCase(unittest.TestCase):
    """把 AzurStats 的库路径指到临时目录，避免碰真实统计库。"""

    INSTANCE = '小号'

    def setUp(self):
        # 不用 TemporaryDirectory：本机 TEMP 指向含非 ASCII 的路径，
        # ``tempfile.gettempdir()`` 会退化成仓库根目录，临时库直接落在工作区里。
        # 这里显式在 log/ 下开目录，用例结束连库带目录一起收掉。
        # ignore_errors 是因为被测代码 ``_ensure_local_db`` 用 ``with sqlite3.connect()``，
        # 退出只 commit 不 close，Windows 上句柄要等 GC 才释放；先 gc 再删。
        tmp_root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'log', '_unittest_opsi')
        os.makedirs(tmp_root, exist_ok=True)
        self._tmp = tempfile.mkdtemp(dir=tmp_root)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.db_path = os.path.join(self._tmp, 'azurstats_local.db')
        patcher = patch.object(AzurStats, 'LOCAL_DB', self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_stat(self, instance=INSTANCE):
        """造一个只带 config_name 的统计对象。"""
        return AzurStats(config=SimpleNamespace(config_name=instance))

    def read_rows(self):
        """按入库顺序读出明细行。

        ``closing`` 而非 ``with connection``：后者只提交事务不关连接，
        Windows 上临时目录会因为句柄没释放而删不掉（PermissionError 噪音）。
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                'SELECT * FROM opsi_items ORDER BY id ASC')]


class RecordLocalWritesInstanceTests(_OpsiStatsTestCase):
    def test_record_local_persists_configured_instance(self):
        """_record_local 能走通解析入口，且写进去的行带当前实例名。

        签名对不上时异常被 except 吞掉、返回 False；instance 列没写时
        落库为 NULL。两个失败点都由这一条锁住。
        """
        stat = self.make_stat()
        with patch.object(AzurStats, '_ensure_local_parser',
                          staticmethod(lambda: _FakeScene)):
            ok = stat._record_local(
                np.zeros((720, 1280, 3), np.uint8),
                'opsi_abyssal',
                '1795000000000.png',
                3,
            )
        self.assertTrue(ok, '入库应返回 True（签名对不上时会被 except 吞成 False）')

        rows = self.read_rows()
        self.assertEqual(len(rows), 1, f'应入库 1 行，实际 {len(rows)} 行')
        self.assertEqual(rows[0]['instance'], self.INSTANCE,
                         '行必须带当前实例名，不能是 NULL')

    def test_two_instances_do_not_mix(self):
        """两个实例先后入库，行各自记各自的名字。"""
        for instance in ('alas', '小号'):
            stat = self.make_stat(instance=instance)
            with patch.object(AzurStats, '_ensure_local_parser',
                              staticmethod(lambda: _FakeScene)):
                self.assertTrue(stat._record_local(
                    np.zeros((720, 1280, 3), np.uint8),
                    'opsi_abyssal',
                    '1795000000000.png',
                    3,
                ))

        instances = [row['instance'] for row in self.read_rows()]
        self.assertEqual(instances, ['alas', '小号'])


class LegacyNullRowMigrationTests(_OpsiStatsTestCase):
    def test_null_rows_are_not_reassigned_after_migration(self):
        """列已存在（迁移早已完成）时，后来的 NULL 行不该被搬给主号。

        老库里全部行都已标好实例，这条 UPDATE 只该在「首次加列」那一次生效；
        否则任何漏写 instance 的路径（例如未来的新写入方）都会被静默改归属。
        """
        AzurStats._ensure_local_db()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                'INSERT INTO opsi_items (imgid, item, amount, genre, created_at) '
                "VALUES ('probe', 'PlateGeneralT4', 1, 'opsi_abyssal', 1795000000)")
            conn.commit()

        AzurStats._ensure_local_db()

        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['instance'],
                          '迁移完成后不应再改写 NULL 行的归属')

    def test_first_migration_assigns_null_rows_to_primary(self):
        """首次加列那次迁移仍然把历史 NULL 行归到实例列表第一个。"""
        patcher = patch('module.config.utils.alas_instance',
                        return_value=['alas', '小号'])
        patcher.start()
        self.addCleanup(patcher.stop)

        # 造一个没有 instance 列的老库
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                'CREATE TABLE opsi_items ('
                'id INTEGER PRIMARY KEY AUTOINCREMENT, imgid TEXT NOT NULL, '
                'server TEXT, zone TEXT, zone_type TEXT, zone_id INTEGER, '
                'hazard_level INTEGER, item TEXT, amount INTEGER, tag TEXT, '
                'device_id TEXT, genre TEXT, combat_count INTEGER, created_at INTEGER)')
            conn.execute(
                "INSERT INTO opsi_items (imgid, item, amount, genre, created_at) "
                "VALUES ('legacy', 'PlateGeneralT4', 1, 'opsi_abyssal', 1795000000)")
            conn.commit()

        AzurStats._ensure_local_db()

        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['instance'], 'alas',
                         '首次迁移应把历史行归到实例列表第一个（主号）')


if __name__ == '__main__':
    unittest.main()
