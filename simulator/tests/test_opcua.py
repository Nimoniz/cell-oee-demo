"""End to end: real OPC UA server + asyncua client, paced at 60x."""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
from pathlib import Path

from asyncua import Client, ua
from conftest import make_config

from cell_sim.__main__ import serve
from cell_sim.config import SimConfig
from cell_sim.model import TAGS, TagType
from cell_sim.persistence import StateStore

_EXPECTED_TYPES = {
    TagType.INT16: ua.VariantType.Int16,
    TagType.UINT32: ua.VariantType.UInt32,
    TagType.FLOAT: ua.VariantType.Float,
    TagType.BOOLEAN: ua.VariantType.Boolean,
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def cfg_for(state: Path | None = None, **overrides) -> SimConfig:
    return make_config(
        opcua={"endpoint": f"opc.tcp://127.0.0.1:{free_port()}"},
        clock={"acceleration": 60},
        persistence={"enabled": state is not None, "path": str(state or "unused.json")},
        **overrides,
    )


def utc(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=dt.UTC) if ts.tzinfo is None else ts


async def wait_for_server(url: str) -> Client:
    for _ in range(50):
        client = Client(url)
        try:
            await client.connect()
            return client
        except OSError:
            await asyncio.sleep(0.1)
    raise AssertionError("server did not come up")


class Collector:
    """Subscription handler keeping (node id, value, source timestamp)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object, dt.datetime]] = []

    def datachange_notification(self, node, val, data) -> None:
        source_ts = data.monitored_item.Value.SourceTimestamp
        self.events.append((node.nodeid.Identifier, val, utc(source_ts)))


def test_address_space_types_and_simulated_timestamps() -> None:
    async def scenario() -> None:
        cfg = cfg_for()
        stop = asyncio.Event()
        server = asyncio.create_task(serve(cfg, stop))
        client = await wait_for_server(cfg.opcua.endpoint)
        try:
            idx = await client.get_namespace_index(cfg.opcua.namespace_uri)
            collector = Collector()
            sub = await client.create_subscription(50, collector)

            nodes = {p: client.get_node(ua.NodeId(p.replace("/", "."), idx)) for p in TAGS}
            for path, tag_type in TAGS.items():
                dv = await nodes[path].read_data_value()
                assert dv.Value.VariantType is _EXPECTED_TYPES[tag_type], path
                assert dv.SourceTimestamp is not None, path

            # The browse path mirrors the PLC data block: Objects/Cell/State, Objects/OP20/Weld/...
            objects = client.nodes.objects
            state = await objects.get_child([f"{idx}:Cell", f"{idx}:State"])
            assert state.nodeid == nodes["Cell/State"].nodeid
            weld = await objects.get_child([f"{idx}:OP20", f"{idx}:Weld", f"{idx}:LastPointOK"])
            assert weld.nodeid == nodes["OP20/Weld/LastPointOK"].nodeid

            await sub.subscribe_data_change([nodes["Cell/Heartbeat"]], queuesize=1000)
            await asyncio.sleep(2.5)  # ~150 simulated seconds at 60x
            await sub.delete()

            beats = [e for e in collector.events if e[0] == "Cell.Heartbeat"]
            assert len(beats) > 60
            start = cfg.clock.start
            stamps = [ts for _, _, ts in beats]
            # Simulated time, not wall-clock time.
            assert all(start <= ts <= start + dt.timedelta(minutes=10) for ts in stamps)
            assert stamps == sorted(stamps)
            values = [int(v) for _, v, _ in beats]
            assert values == sorted(values)
            # One tick per simulated second: no sample lost by the queue.
            gaps = {
                round((b - a).total_seconds(), 3) for a, b in zip(stamps, stamps[1:], strict=False)
            }
            assert gaps == {1.0}
        finally:
            await client.disconnect()
            stop.set()
            await server

    asyncio.run(scenario())


def test_stop_origin_shares_the_state_timestamp_over_opcua() -> None:
    async def scenario() -> None:
        # Frequent short starved periods, so state changes happen within a couple of seconds.
        cfg = cfg_for(flow={"starved": {"rate_per_h": 120}})
        stop = asyncio.Event()
        server = asyncio.create_task(serve(cfg, stop))
        client = await wait_for_server(cfg.opcua.endpoint)
        try:
            idx = await client.get_namespace_index(cfg.opcua.namespace_uri)
            collector = Collector()
            sub = await client.create_subscription(50, collector)
            await sub.subscribe_data_change(
                [
                    client.get_node(ua.NodeId("Cell.State", idx)),
                    client.get_node(ua.NodeId("Cell.StopOrigin", idx)),
                ],
                queuesize=1000,
            )
            await asyncio.sleep(3)
            await sub.delete()
            by_ts: dict[dt.datetime, set[str]] = {}
            for path, _, ts in collector.events:
                by_ts.setdefault(ts, set()).add(str(path))
            state_ts = {ts for ts, paths in by_ts.items() if "Cell.State" in paths}
            origin_ts = {ts for ts, paths in by_ts.items() if "Cell.StopOrigin" in paths}
            assert len(state_ts) >= 3
            assert origin_ts <= state_ts
        finally:
            await client.disconnect()
            stop.set()
            await server

    asyncio.run(scenario())


def test_clean_shutdown_saves_state_and_restart_never_goes_back(tmp_path: Path) -> None:
    async def run_once(state: Path) -> dt.datetime:
        cfg = cfg_for(state)
        stop = asyncio.Event()
        server = asyncio.create_task(serve(cfg, stop))
        client = await wait_for_server(cfg.opcua.endpoint)
        try:
            idx = await client.get_namespace_index(cfg.opcua.namespace_uri)
            node = client.get_node(ua.NodeId("Cell.Heartbeat", idx))
            first = await node.read_data_value()
            await asyncio.sleep(1.5)
            last = await node.read_data_value()
            assert last.Value.Value > first.Value.Value
            return utc(first.SourceTimestamp)
        finally:
            await client.disconnect()
            stop.set()
            await server

    async def scenario() -> None:
        state = tmp_path / "state" / "plc.json"
        first_seen = await run_once(state)
        saved = StateStore(state).load()
        assert saved is not None and saved.clean_shutdown
        assert saved.heartbeat > 0
        second_seen = await run_once(state)
        # The second run starts where the first stopped: simulated time did not go backwards.
        assert second_seen.timestamp() >= saved.sim_time - 1.0
        assert second_seen > first_seen

    asyncio.run(scenario())
