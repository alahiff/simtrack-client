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

class TestArtifactsCreatedState(unittest.TestCase):
    def test_artifact_code_created(self):
        """
        Create a run & an artifact of type 'code' & check it can be downloaded for
        a run left in the created state
        """
        run = Run()
        run.init(common.RUNNAME1, folder=common.FOLDER, running=False)

        content = str(uuid.uuid4())
        with open(common.FILENAME1, 'w') as fh:
            fh.write(content)
        run.save(common.FILENAME1, 'code')

        shutil.rmtree('./test', ignore_errors=True)
        os.mkdir('./test')

        client = Client()
        client.get_artifact_as_file(common.RUNNAME1, common.FILENAME1, './test')

        self.assertTrue(filecmp.cmp(common.FILENAME1, './test/%s' % common.FILENAME1))

        runs = client.delete_runs(common.FOLDER)
        self.assertEqual(len(runs), 1)

        shutil.rmtree('./test', ignore_errors=True)
        os.remove(common.FILENAME1)

if __name__ == '__main__':
    unittest.main()
