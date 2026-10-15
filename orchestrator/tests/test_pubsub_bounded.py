"""PubSub queues are bounded so a stalled subscriber cannot grow server memory without limit."""
from orchestrator.pubsub import OVERFLOW, PubSub


def test_a_slow_subscriber_is_dropped_with_an_overflow_marker():
    pubsub = PubSub(queue_max=3)
    slow = pubsub.subscribe()
    fast = pubsub.subscribe()
    for i in range(3):
        pubsub.publish({"type": "message", "data": {"seq": i}})
    fast_seen = [fast.get_nowait() for _ in range(3)]  # the fast one keeps up
    assert [e["data"]["seq"] for e in fast_seen] == [0, 1, 2]

    pubsub.publish({"type": "message", "data": {"seq": 3}})  # slow's queue is full

    assert slow.qsize() == 1 and slow.get_nowait() == OVERFLOW  # backlog dropped, marker left
    pubsub.publish({"type": "message", "data": {"seq": 4}})
    assert slow.empty()  # and it is no longer subscribed
    assert fast.get_nowait()["data"]["seq"] == 3 and fast.get_nowait()["data"]["seq"] == 4


def test_an_unbounded_subscriber_never_overflows():
    pubsub = PubSub(queue_max=2)
    q = pubsub.subscribe(bounded=False)
    for i in range(50):
        pubsub.publish({"type": "message", "data": {"seq": i}})
    assert q.qsize() == 50 and q.get_nowait()["data"]["seq"] == 0
