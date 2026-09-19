"""验证配置事务在进程间互斥，并保留运行器之外的并发修改。"""
import json
import copy
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from module.config.transaction import config_transaction


def increment(path, count):
    for _ in range(count):
        with config_transaction(path):
            file = Path(path)
            data = json.loads(file.read_text())
            data['value'] += 1
            file.write_text(json.dumps(data))


class ConfigTransactionTests(unittest.TestCase):
    def test_stale_worker_cannot_undo_same_field_edit(self):
        from module.config.config import AzurLaneConfig
        from module.api.config_service import ConfigService
        from module.api.protocol import ConfigChange
        from tests.test_api import fixture

        for reload_before_save in (False, True):
            with self.subTest(reload_before_save=reload_before_save), tempfile.TemporaryDirectory() as directory:
                service = ConfigService(fixture(directory))
                worker = AzurLaneConfig.__new__(AzurLaneConfig)
                worker.config_name = 'testpilot'
                path = str(service.path('testpilot'))
                with patch('module.config.config.filepath_config', return_value=path), patch(
                    'module.config.config_updater.filepath_config', return_value=path
                ), patch.object(AzurLaneConfig, 'config_override'):
                    worker.data = worker.read_file('testpilot')
                    worker._loaded_data = copy.deepcopy(worker.data)
                    worker.modified = {'Alas.Emulator.Serial': 'stale-worker', 'Main.Scheduler.Enable': True}
                    service.patch('testpilot', None, [ConfigChange(path='Alas.Emulator.Serial', value='user-edit')])
                    if reload_before_save:
                        worker.load()
                    worker.save()
                saved = service.get('testpilot')['values']
                self.assertEqual('user-edit', saved['Alas']['Emulator']['Serial'])
                self.assertTrue(saved['Main']['Scheduler']['Enable'])

    def test_stale_worker_save_preserves_frontend_edit(self):
        from module.config.config import AzurLaneConfig
        from module.api.config_service import ConfigService
        from module.api.protocol import ConfigChange
        from tests.test_api import fixture

        with tempfile.TemporaryDirectory() as directory:
            service = ConfigService(fixture(directory))
            original = service.get('testpilot')
            worker = AzurLaneConfig.__new__(AzurLaneConfig)
            worker.config_name = 'testpilot'
            worker.data = original['values']
            worker.modified = {'Main.Scheduler.Enable': True}
            pending = worker.modified
            service.patch('testpilot', original['revision'], [
                ConfigChange(path='Alas.Emulator.Serial', value='frontend-edit'),
            ])
            path = str(service.path('testpilot'))
            with patch('module.config.config.filepath_config', return_value=path), patch(
                'module.config.config_updater.filepath_config', return_value=path
            ):
                worker.save()
            saved = service.get('testpilot')['values']
            self.assertEqual('frontend-edit', saved['Alas']['Emulator']['Serial'])
            self.assertTrue(saved['Main']['Scheduler']['Enable'])
            self.assertIs(pending, worker.modified)
            self.assertEqual({}, pending)

    def test_processes_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.json'
            path.write_text('{"value": 0}')
            context = multiprocessing.get_context('spawn')
            processes = [context.Process(target=increment, args=(str(path), 30)) for _ in range(3)]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=15)
                    self.assertEqual(0, process.exitcode)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=3)
            self.assertEqual(90, json.loads(path.read_text())['value'])

    def test_nested_same_thread_lock_is_reentrant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.json'
            with config_transaction(path), config_transaction(path):
                path.write_text('{}')
            self.assertEqual('{}', path.read_text())


if __name__ == '__main__':
    unittest.main()
