import configparser
import filecmp
import os
import shutil
import time
import unittest
import uuid
from simvue import Run, Client
from simvue.sender import sender

import common

class TestArtifacts(unittest.TestCase):
    def test_artifact_code(self):
        """
        Create a run & an artifact of type 'code' & check it can be downloaded
        """
        run = Run()
        folder = '/test-%s' % str(uuid.uuid4())
        run.init(common.RUNNAME1, folder=folder)

        content = str(uuid.uuid4())
        with open(common.FILENAME1, 'w') as fh:
            fh.write(content)
        run.save(common.FILENAME1, 'code')

        run.close()

        run_id = common.RUNNAME1
        if common.SIMVUE_API_VERSION:
            run_id = run.id

        shutil.rmtree('./test', ignore_errors=True)
        os.mkdir('./test')

        client = Client()
        client.get_artifact_as_file(run_id, common.FILENAME1, './test')

        self.assertTrue(filecmp.cmp(common.FILENAME1, './test/%s' % common.FILENAME1))

        runs = client.delete_runs(folder)
        self.assertEqual(len(runs), 1)

        shutil.rmtree('./test', ignore_errors=True)
        os.remove(common.FILENAME1)

if __name__ == '__main__':
    unittest.main()
