import unittest
import uuid
from simvue import Run, Client

import common

class TestRunMetadataUpdatedCreated(unittest.TestCase):
    def test_run_metadata_update_created(self):
        """
        Check metadata can be updated & retrieved
        """
        name = 'test-%s' % str(uuid.uuid4())
        metadata = {'a': 'string', 'b': 1, 'c': 2.5}
        run = Run()
        run.init(name, metadata=metadata, folder=common.FOLDER, running=False)
        run.update_metadata({'b': 2})

        metadata['b'] = 2

        run_id = name
        if common.SIMVUE_API_VERSION:
            run_id = run.id

        client = Client()
        data = client.get_run(run_id, metadata=True)
        self.assertEqual(data['metadata'], metadata)

        runs = client.delete_runs(common.FOLDER)
        self.assertEqual(len(runs), 1)

if __name__ == '__main__':
    unittest.main()
