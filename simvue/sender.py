import glob
import json
import logging
import os
import time

from .remote import Remote
from .utilities import get_offline_directory, create_file, remove_file

logger = logging.getLogger(__name__)

def get_json(filename):
    """
    Get JSON from a file
    """
    with open(filename, 'r') as fh:
        data = json.load(fh)
    return data

def get_binary(filename):
    """
    Get binary content from a file
    """
    with open(filename, 'rb') as fh:
        data = fh.read()
    return data

def sender():
    """
    Asynchronous upload of runs to Simvue server
    """
    directory = get_offline_directory()

    # Deal with runs in the running or completed state
    runs = glob.glob(f"{directory}/*/running") + glob.glob(f"{directory}/*/completed")
    for run in runs:
        status = None
        if run.endswith('running'):
            status = 'running'
        elif run.endswith('completed'):
            status = 'completed'

        current = run.replace('/running', '').replace('/completed', '')

        id = run.split('/')[len(run.split('/')) - 2]

        run_init = get_json(f"{current}/run.json")
        start_time = os.path.getctime(f"{current}/run.json")

        logger.info('Considering run with name %s and id %s', run_init['name'], id)

        remote = Remote(run_init['name'], suppress_errors=True)

        # Create run if it hasn't previously been created
        created_file = f"{current}/created"
        if not os.path.isfile(created_file):
            logger.info('Creating run with name %s', run_init['name'])
            remote.create_run(run_init)
            create_file(created_file)

        if status == 'running':
            # Check for recent heartbeat
            heartbeat_filename = f"{current}/heartbeat"
            if os.path.isfile(heartbeat_filename):
                mtime = os.path.getmtime(heartbeat_filename)
                if time.time() - mtime > 180:
                    status = 'lost'

            # Check for no recent heartbeat
            if not os.path.isfile(heartbeat_filename):
                if time.time() - start_time > 180:
                    status = 'lost'

        # Handle lost runs
        if status == 'lost':
            logger.info('Changing status to lost, name %s and id %s', run_init['name'], id)
            status = 'lost'
            create_file(f"{current}/lost")
            remove_file(f"{current}/running")

        # Send heartbeat if necessary
        if status == 'running':
            logger.info('Sending heartbeat for run with name %s', run_init['name'])
            remote.send_heartbeat()

        # Upload metrics, events, files & metadata as necessary
        files = sorted(glob.glob(f"{current}/*"), key=os.path.getmtime)
        updates = 0
        for record in files:
            if record.endswith('/run.json') or \
               record.endswith('/running') or \
               record.endswith('/completed') or \
               record.endswith('/lost') or \
               record.endswith('/sent') or \
               record.endswith('-proc'):
                continue

            rename = False

            # Handle metrics
            if '/metrics-' in record:
                logger.info('Sending metrics for run %s', run_init['name'])
                remote.send_metrics(get_binary(record))
                rename = True

            # Handle events
            if '/event-' in record:
                logger.info('Sending event for run %s', run_init['name'])
                remote.send_event(get_binary(record))
                rename = True

            # Handle updates
            if '/update-' in record:
                logger.info('Sending update for run %s', run_init['name'])
                remote.update(get_json(record))
                rename = True

            # Handle folders
            if '/folder-' in record:
                logger.info('Sending folder details for run %s', run_init['name'])
                remote.set_folder_details(get_json(record))
                rename = True

            # Handle alerts
            if '/alert-' in record:
                logger.info('Sending alert details for run %s', run_init['name'])
                remote.add_alert(get_json(record))
                rename = True

            # Handle files
            if '/file-' in record:
                logger.info('Saving file for run %s', run_init['name'])
                remote.save_file(get_json(record))
                rename = True

            # Rename processed files
            if rename:
                os.rename(record, f"{record}-proc")
                updates += 1

        # If the status is completed and there were no updates, the run must have completely finished
        if updates == 0 and status == 'completed':
            logger.info('Finished sending run %s', run_init['name'])
            create_file(f"{current}/sent")
            remove_file(f"{current}/completed")
            data = {'name': run_init['name'], 'status': 'completed'}
            remote.update(data)