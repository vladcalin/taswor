import os
import sys
import json
import shutil
from multiprocessing import Queue, Event, Process, RLock, Manager
import time
import copy

from taswor.util import Next, get_logger, preprocess_events
from taswor.node import Node
from taswor.process.worker import worker_run


class Workflow:
    def __init__(self, *nodes, workers=os.cpu_count(), cache_url=None, storage_url=None):
        self.nodes = nodes
        self.queue = Queue()
        self.queue_lock = RLock()
        self.is_idle = [Event() for _ in range(workers)]
        self.logger = get_logger("WorkflowMain")

        self.manager = Manager()
        self.events = self.manager.list()

        if not cache_url:
            self.logger.warning("No cache backend supplied. Results will not be cached and may have a serious "
                                "performance impact")
        if not storage_url:
            self.logger.warning("No storage backend supplied. Storage usage inside the Node instances will be ignored")

        self.logger.info("Starting workers")
        self.workers = [
            Process(target=worker_run,
                    args=(self.is_idle[i], self.queue, self.queue_lock, self.nodes, self.events),
                    name="worker-{}".format(i))
            for i in range(workers)]

        for worker in self.workers:
            worker.start()

    def start(self, wait=False):
        start_nodes = self._get_start_nodes()

        for node in start_nodes:
            if not node.init_generator:
                self.queue.put((node, (), {}))
            else:
                node_copy = copy.deepcopy(node)
                node_copy.init_generator = None
                for args, kwargs in node.init_generator:
                    print(args, kwargs)
                    self.queue.put((node_copy, args, kwargs))

        if wait:
            self.wait_for_completion()

    def close(self):
        self.logger.info("Closing all workers")
        for worker in self.workers:
            worker.terminate()
            worker.join()

        self.logger.info("All workers killed")

        self.logger.info("Terminating")

    def wait_for_completion(self):
        self.logger.debug("Waiting for completion")
        finished = False
        while not finished:
            if self._all_workers_are_idle():
                self.logger.info("Finished")
                finished = True

    def dump_result_as_json(self, filename):
        import json
        with open(filename, "w") as out:
            data = {"raport": [x.to_dict() for x in self.events]}
            json.dump(data, out, indent=4, sort_keys=True)

    def dump_result_as_html(self, directory):
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "html")
        if os.path.exists(directory):
            shutil.rmtree(directory)
        shutil.copytree(template_dir, directory)

        def get_label(node_name, args, kwargs):
            args = [str(arg) for arg in args]
            kwargs = {k: str(v) for k, v in kwargs.items()}
            arguments = ", ".join(args + ["{}={}".format(k, v) for k, v in kwargs.items()])
            return node_name + " (" + arguments + ")"

        data = {
            "nodes": {},
            "edges": {}
        }

        nodes, edges = preprocess_events(self.events)
        data["nodes"] = nodes
        data["edges"] = edges

        with open(os.path.join(directory, "data.json"), "w") as data_json:
            data_json.write("var data = ")
            data_json.write(json.dumps(data, indent=4))
            data_json.write(";")

    def _get_start_nodes(self):
        return [node for node in self.nodes if node.start]

    def _all_workers_are_idle(self):
        time.sleep(1)
        x = [event.is_set() for event in self.is_idle]
        return all(x)


def node(retries=1, start=False, init_args=None):
    def decorator(func):
        node = Node(name=func.__name__, func=func, start=start, init_generator=init_args, retries=retries)
        func.node = node
        return func

    return decorator
