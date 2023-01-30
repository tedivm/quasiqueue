import logging
import time
from queue import Full


class Builder:
    def __init__(self, queue, settings, writer):
        self.i = 0
        self.queue = queue
        self.settings = settings
        self.last_queued = {}
        self.writer = writer
        self.closed = False

    async def populate(self, max=50):

        self.clean_history()

        # Writers can be expensive but cheaper when pulling bulk records.
        queue_size = self.queue.qsize()
        if queue_size >= self.settings.max_queue_size * 0.3:
            return True

        # Don't try to fill the queue 100% since the queue size isn't always accurate.
        count = min(int(self.settings.max_queue_size * 0.8) - queue_size, max)
        blocksize = min(self.settings.lookup_block_size, count)
        if count <= 0:
            logging.debug("Skipping queue population due to max queue size.")
            return False
        try:
            successful_adds = 0

            # If the queue is closed tell the children processes to close.
            if self.closed:
                for i in range(0, blocksize):
                    self.queue.put("close", True, self.settings.queue_interaction_timeout)
                return False

            async for id in self.writer(desired=blocksize):
                if id is None or id is False:
                    logging.debug(f"Returning False {id}")
                    return False
                if self.add_to_queue(id):
                    logging.debug(f"Added {id} to queue.")
                    successful_adds += 1
                    if successful_adds >= max:
                        return True
        except Full as e:
            logging.debug("Queue has reached max size.")
            return False

    def add_to_queue(self, id):
        if id in self.last_queued:

            logging.debug(f"ID {id} is in last_queued")
            logging.debug(time.time())
            logging.debug(self.last_queued[id] + self.settings.prevent_requeuing_time)

            if self.last_queued[id] + self.settings.prevent_requeuing_time > time.time():
                logging.debug(f"Skipping {id}: added too recently.")
                return False
        logging.debug(f"Adding {id} to queue.")
        self.last_queued[id] = time.time()
        self.queue.put(id, True, self.settings.queue_interaction_timeout)
        return True

    def clean_history(self):
        self.last_queued = {
            # Keep item as long as that item expires in the future. Items which have already expired will be removed.
            k: v
            for k, v in self.last_queued.items()
            if v + self.settings.prevent_requeuing_time > time.time()
        }

    def close(self):
        if self.closed:
            return False
