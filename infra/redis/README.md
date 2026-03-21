# GraphClaw Redis Cluster

This directory contains the Redis Cluster configuration and tooling for GraphClaw's
cache tier. Local development uses a single-node Redis instance (see
`docker/docker-compose.yml`). The cluster configuration here is for staging and
production deployments.

---

## Why 3 Nodes Are the Minimum

Redis Cluster uses a gossip protocol and requires a quorum of masters to remain
available. The minimum viable cluster is **3 master nodes**. With fewer than 3
masters:

- The cluster cannot achieve quorum to elect a replacement master when one node fails.
- `redis-cli --cluster create` will refuse to create the cluster.
- The `CLUSTER INFO` state will report `cluster_state:fail`.

A 3-master, 0-replica cluster (`--cluster-replicas 0`) tolerates the failure of
**0** masters before the cluster enters a failed state. It is suitable for
development and staging but not for production SLAs that require high availability.

---

## Consistent Hashing with `{USER-<id>}` Hash Tags

Redis Cluster distributes keys across 16384 hash slots. By default each key is
hashed independently, which means related keys may land on different shards and
cannot participate in multi-key commands (`MGET`, `MSET`, `EVAL`, transactions).

GraphClaw uses **hash tags** to co-locate all keys belonging to a single user on
the same shard. A hash tag is a substring enclosed in `{}`. Redis computes the
slot from the tag content only.

Example key format:

```
{USER-abc123}:sessions
{USER-abc123}:task-cache
{USER-abc123}:rate-limit
```

All three keys hash to the same slot because they share the tag `USER-abc123`.
This enables atomic operations across a user's keyspace and simplifies cache
invalidation (one `SCAN` on the owning shard is sufficient).

The pattern is enforced via `RedisClusterConfig.user_hash_tag_pattern = "{USER-"`.

---

## Upgrade Path: 3 Masters + 3 Replicas (Full HA)

To add one replica per master, replace the `CLUSTER_NODES` tuple with 6 nodes
(3 masters + 3 replicas) and run:

```sh
redis-cli --cluster create \
  redis-1:6379 redis-2:6379 redis-3:6379 \
  redis-4:6379 redis-5:6379 redis-6:6379 \
  --cluster-replicas 1 --cluster-yes
```

With `--cluster-replicas 1` the cluster can survive the loss of one master per
shard. Update `infra/scaling/profiles.py` `cache.max_tasks` from 9 to 9 (already
sized for 3+3).

---

## Initialising the Cluster

After all three Redis containers are running and reachable:

```sh
redis-cli --cluster create \
  redis-1:6379 redis-2:6379 redis-3:6379 \
  --cluster-replicas 0 --cluster-yes
```

Or use `get_cluster_meet_commands` from `infra.redis.redis_conf` to generate the
command programmatically:

```python
from infra.redis.cluster_config import DEFAULT_CLUSTER_CONFIG
from infra.redis.redis_conf import get_cluster_meet_commands

for cmd in get_cluster_meet_commands(DEFAULT_CLUSTER_CONFIG):
    print(cmd)
```

Verify the cluster is healthy:

```sh
redis-cli -h redis-1 -p 6379 cluster info
```

---

## Client-Side Usage (redis-py RedisCluster)

```python
from redis.cluster import RedisCluster

rc = RedisCluster(
    host="redis-1",
    port=6379,
    decode_responses=True,
)

# All keys for user abc123 land on the same shard
rc.set("{USER-abc123}:session", "tok_xyz", ex=900)
rc.set("{USER-abc123}:rate-limit", "0", ex=60)

# Multi-key get works because both keys share the same hash slot
values = rc.mget("{USER-abc123}:session", "{USER-abc123}:rate-limit")
```

Use `startup_nodes` for production when node addresses are discovered dynamically:

```python
from redis.cluster import RedisCluster, ClusterNode

startup_nodes = [
    ClusterNode("redis-1", 6379),
    ClusterNode("redis-2", 6379),
    ClusterNode("redis-3", 6379),
]
rc = RedisCluster(startup_nodes=startup_nodes, decode_responses=True)
```

---

## Local Development

Local dev uses a **single-node Redis** instance defined in
`docker/docker-compose.yml`. Cluster mode is not active; the `REDIS_URL`
environment variable points to `redis://redis:6379`.

The cluster configuration in this directory (`infra/redis/`) is used only by
staging and production deployments. Do not attempt to run `redis-cli --cluster
create` against the local dev container — it will fail because cluster mode is
not enabled on the single-node image.

To test cluster behaviour locally, set `cluster-enabled yes` in a custom
redis.conf and start three Redis containers manually, or use the `generate_redis_conf`
helper from `infra.redis.redis_conf`.
