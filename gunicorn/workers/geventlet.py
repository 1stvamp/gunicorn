# -*- coding: utf-8 -
#
# This file is part of gunicorn released under the MIT license.
# See the NOTICE for more information.

from functools import partial
import os

try:
    import eventlet
except ImportError:
    raise RuntimeError("You need eventlet installed to use this worker.")
from eventlet import hubs
from eventlet.greenio import GreenSocket

from gunicorn.workers.async import AsyncWorker

class EventletWorker(AsyncWorker):

    @classmethod
    def setup(cls):
        import eventlet
        if eventlet.version_info < (0,9,7):
            raise RuntimeError("You need eventlet >= 0.9.7")
        eventlet.monkey_patch(os=False)

    def init_process(self):
        hubs.use_hub()
        super(EventletWorker, self).init_process()

    def timeout_ctx(self):
        return eventlet.Timeout(self.cfg.keepalive or None, False)

    def run(self):
        acceptors = []
        for sock in self.sockets:
            s = GreenSocket(family_or_realsock=sock)
            s.setblocking(1)
            hfun = partial(self.handle, s)
            acceptor = eventlet.spawn(eventlet.serve, s, hfun,
                    self.worker_connections)

        while self.alive:
            self.notify()
            if self.ppid != os.getppid():
                self.log.info("Parent changed, shutting down: %s", self)
                break

            eventlet.sleep(1.0)

        self.notify()
        with eventlet.Timeout(self.cfg.graceful_timeout, False):
            [eventlet.kill(a, eventlet.StopServe) for a in acceptors]
