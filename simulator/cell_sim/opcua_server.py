"""OPC UA address space of the simulated PLC.

Mirrors a data block exposed by an S7-1500's OPC UA server: string node ids such as
``ns=2;s=Cell.State``, browse path ``Objects/Cell/State``. Every write carries the *simulated*
time as SourceTimestamp; the wall clock never reaches a client.
"""

from __future__ import annotations

from types import TracebackType

from asyncua import Server, ua

from .config import OpcUaCfg
from .model import TAGS, TagChange, TagType, TagValue, to_datetime

_VARIANT_TYPES: dict[TagType, ua.VariantType] = {
    TagType.INT16: ua.VariantType.Int16,
    TagType.UINT32: ua.VariantType.UInt32,
    TagType.FLOAT: ua.VariantType.Float,
    TagType.BOOLEAN: ua.VariantType.Boolean,
}

_DEFAULTS: dict[TagType, TagValue] = {
    TagType.INT16: 0,
    TagType.UINT32: 0,
    TagType.FLOAT: 0.0,
    TagType.BOOLEAN: False,
}


class OpcUaPlc:
    def __init__(self, cfg: OpcUaCfg) -> None:
        self._cfg = cfg
        self._server = Server()
        self._nodes: dict[str, ua.NodeId] = {}
        self._namespace_idx = 0

    @property
    def namespace_idx(self) -> int:
        return self._namespace_idx

    def node_id(self, path: str) -> ua.NodeId:
        return self._nodes[path]

    async def __aenter__(self) -> OpcUaPlc:
        await self.init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def init(self) -> None:
        server = self._server
        await server.init()
        server.set_endpoint(self._cfg.endpoint)
        server.set_server_name("Cell OEE Demo PLC")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self._namespace_idx = await server.register_namespace(self._cfg.namespace_uri)
        await self._build_address_space()

    async def start(self) -> None:
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()

    async def _build_address_space(self) -> None:
        idx = self._namespace_idx
        objects = self._server.nodes.objects
        folders: dict[str, object] = {}

        async def folder_for(path: str):  # e.g. "OP20/Weld"
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            parent = await folder_for(parent_path) if parent_path else objects
            folder = await parent.add_object(  # type: ignore[attr-defined]
                ua.NodeId(path.replace("/", "."), idx), ua.QualifiedName(name, idx)
            )
            folders[path] = folder
            return folder

        for path, tag_type in TAGS.items():
            folder_path, _, name = path.rpartition("/")
            folder = await folder_for(folder_path)
            node_id = ua.NodeId(path.replace("/", "."), idx)
            await folder.add_variable(  # type: ignore[attr-defined]
                node_id,
                ua.QualifiedName(name, idx),
                ua.Variant(_DEFAULTS[tag_type], _VARIANT_TYPES[tag_type]),
                varianttype=_VARIANT_TYPES[tag_type],
            )
            self._nodes[path] = node_id

    async def publish(self, changes: list[TagChange]) -> None:
        """Write tag changes, in order, each stamped with its simulated SourceTimestamp."""
        write = self._server.write_attribute_value
        for c in changes:
            variant = ua.Variant(c.value, _VARIANT_TYPES[TAGS[c.path]])
            dv = ua.DataValue(variant, SourceTimestamp=to_datetime(c.ts))
            await write(self._nodes[c.path], dv)
