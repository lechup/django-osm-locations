# -*- coding: utf-8 -*-


class MemoryCache(object):
    def __init__(self, *args, **kwargs):
        # used as a cache for ids of nodes we need to cache that
        # are part of relations and ways
        self.precache = set()
        self.keys_cache = set()
        self.cache = {}

    def set(self, key, value, *args, **kwargs):
        self.cache[key] = value
        self.keys_cache.add(key)

    def get(self, key, *args, **kwargs):
        return self.cache[key]

    def flushall(self):
        pass

    def flushdb(self):
        pass

    def exists(self, key):
        return key in self.keys_cache

    def keys(self):
        return list(self.keys_cache)
