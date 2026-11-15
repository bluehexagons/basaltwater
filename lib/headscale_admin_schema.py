"""Wire-compatible subset of official Headscale v0.29.7 headscale.v1 protos.

Field numbers/types come from proto/headscale/v1/{user,node,auth,preauthkey}.proto
at https://github.com/juanfont/headscale/tree/v0.29.7/proto/headscale/v1.
No generated runtime/version lock; distro protobuf is loaded only on use.
Unknown native fields are discarded by the output projection, never forwarded.
"""

from __future__ import annotations


def messages() -> dict:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, timestamp_pb2

    file = descriptor_pb2.FileDescriptorProto(name="basaltwater_headscale_subset.proto",
                                             package="headscale.v1", syntax="proto3")
    file.dependency.append("google/protobuf/timestamp.proto")
    # (name, number, scalar or qualified message type, repeated)
    definitions = {
        "User": [("id", 1, "uint64"), ("name", 2, "string")],
        "Node": [("id", 1, "uint64"), ("ip_addresses", 5, "string", True),
                 ("name", 6, "string"), ("user", 7, "User"),
                 ("expiry", 10, "Timestamp"), ("pre_auth_key", 11, "PreAuthKey"), ("tags", 26, "string", True),
                 ("online", 22, "bool")],
        "PreAuthKey": [("user", 1, "User"), ("id", 2, "uint64"),
                       ("key", 3, "string"), ("reusable", 4, "bool"),
                       ("ephemeral", 5, "bool"), ("used", 6, "bool"),
                       ("expiration", 7, "Timestamp"), ("acl_tags", 9, "string", True)],
        "ListUsersRequest": [], "ListUsersResponse": [("users", 1, "User", True)],
        "CreateUserRequest": [("name", 1, "string")],
        "CreateUserResponse": [("user", 1, "User")],
        "DeleteUserRequest": [("id", 1, "uint64")], "DeleteUserResponse": [],
        "ListNodesRequest": [], "ListNodesResponse": [("nodes", 1, "Node", True)],
        "AuthRegisterRequest": [("user", 1, "string"), ("auth_id", 2, "string")],
        "AuthRegisterResponse": [("node", 1, "Node")],
        "ExpireNodeRequest": [("node_id", 1, "uint64")],
        "ExpireNodeResponse": [("node", 1, "Node")],
        "DeleteNodeRequest": [("node_id", 1, "uint64")], "DeleteNodeResponse": [],
        "CreatePreAuthKeyRequest": [("expiration", 4, "Timestamp"),
                                    ("acl_tags", 5, "string", True)],
        "CreatePreAuthKeyResponse": [("pre_auth_key", 1, "PreAuthKey")],
        "ListPreAuthKeysRequest": [],
        "ListPreAuthKeysResponse": [("pre_auth_keys", 1, "PreAuthKey", True)],
        "ExpirePreAuthKeyRequest": [("id", 1, "uint64")], "ExpirePreAuthKeyResponse": [],
        "DeletePreAuthKeyRequest": [("id", 1, "uint64")], "DeletePreAuthKeyResponse": [],
    }
    for name, fields in definitions.items():
        message = file.message_type.add(name=name)
        for spec in fields:
            field_name, number, kind, *repeated = spec
            field = message.field.add(name=field_name, number=number,
                                      label=3 if repeated else 1)
            scalar = {"uint64": 4, "string": 9, "bool": 8}
            field.type = scalar.get(kind, 11)
            if kind not in scalar:
                field.type_name = (".google.protobuf.Timestamp" if kind == "Timestamp"
                                   else ".headscale.v1." + kind)
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(timestamp_pb2.DESCRIPTOR.serialized_pb)
    pool.Add(file)
    factory = message_factory.MessageFactory(pool)
    def make(name):
        descriptor = pool.FindMessageTypeByName("headscale.v1." + name)
        if hasattr(message_factory, "GetMessageClass"):
            return message_factory.GetMessageClass(descriptor)
        return factory.GetPrototype(descriptor)
    return {name: make(name) for name in definitions}
