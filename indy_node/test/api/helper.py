import random
import json
import base58

from indy.anoncreds import issuer_create_schema
from indy.ledger import build_schema_request, sign_request, submit_request

from indy_common.state.state_constants import MARKER_CONTEXT
from plenum.common.constants import TXN_TYPE, DATA, CURRENT_PROTOCOL_VERSION

from plenum.test.helper import sdk_get_reply, sdk_sign_and_submit_req, sdk_get_and_check_replies


def req_id():
    id = random.randint(1, 100000000)
    while True:
        yield id
        id += 1


_reqId = req_id()
# Utility predicates


def is_one_of(*args):
    def check(v):
        return v in args

    return check


def is_int(v):
    return isinstance(v, int)


def is_list(v):
    return isinstance(v, list)


def is_dict(v):
    return isinstance(v, dict)


def is_str(v):
    return isinstance(v, str)


def is_base58_str(v):
    return is_str(v) and all(c in base58.alphabet for c in v.encode())


def is_number_str(v):
    return is_str(v) and v.isdigit()


def is_did(v):
    return is_base58_str(v) and len(base58.b58decode(v)) in [16, 32]


def is_verkey(v):
    if not is_str(v):
        return False
    key_len = 32
    if v.startswith('~'):
        key_len = 16
        v = v[1:]
    return is_base58_str(v) and len(base58.b58decode(v)) == key_len


def is_json(v):
    try:
        json.loads(v)
        return True
    except:
        return False


def is_sha256(v):
    return is_str(v) \
           and all(c in '0123456789abcdef' for c in v) \
           and len(v) == 256 // 4


# Basic requirement checkers

def require(d, k, t):
    assert k in d
    assert t(d[k])


def optional(d, k, t):
    if k in d:
        assert t(d[k])


# Generic validators

def validate_txn(txn):
    require(txn, 'type', is_number_str)
    require(txn, 'data', is_dict)
    require(txn, 'metadata', is_dict)
    optional(txn, 'protocolVersion', is_int)

    metadata = txn['metadata']
    require(metadata, 'from', is_did)
    require(metadata, 'reqId', is_int)


def validate_txn_metadata(txn_metadata):
    require(txn_metadata, 'txnTime', is_int)
    require(txn_metadata, 'seqNo', is_int)
    require(txn_metadata, 'txnId', is_str)


def validate_req_signature(req_signature):
    require(req_signature, 'type', is_one_of('ED25519', 'ED25519_MULTI'))
    require(req_signature, 'values', is_list)
    for value in req_signature['values']:
        assert is_dict(value)
        require(value, 'from', is_did)
        require(value, 'value', is_base58_str)


def validate_write_result(result):
    require(result, 'ver', is_str)
    require(result, 'txn', is_dict)
    require(result, 'txnMetadata', is_dict)
    require(result, 'reqSignature', is_dict)
    require(result, 'rootHash', is_base58_str)
    require(result, 'auditPath', is_list)
    assert all(is_base58_str(v) for v in result['auditPath'])

    validate_txn(result['txn'])
    validate_txn_metadata(result['txnMetadata'])
    validate_req_signature(result['reqSignature'])


def validate_write_reply(reply):
    require(reply, 'op', is_one_of('REPLY'))
    require(reply, 'result', is_dict)
    validate_write_result(reply['result'])


# Specific txn validators

def validate_nym_txn(txn):
    require(txn, 'type', is_one_of('1'))

    data = txn['data']
    require(data, 'dest', is_did)
    optional(data, 'role', is_one_of(None, '0', '2', '101'))
    optional(data, 'verkey', is_verkey)
    optional(data, 'alias', is_str)


def validate_attrib_txn(txn):
    require(txn, 'type', is_one_of('100'))

    data = txn['data']
    require(data, 'dest', is_did)
    optional(data, 'raw', is_json)
    optional(data, 'hash', is_sha256)
    optional(data, 'enc', is_str)
    assert sum(1 for k in data.keys() if k in ['raw', 'hash', 'enc']) == 1


def validate_schema_txn(txn):
    require(txn, 'type', is_one_of('101'))

    data = txn['data']
    require(data, 'data', is_dict)

    data = data['data']
    require(data, 'name', is_str)
    require(data, 'version', is_str)
    require(data, 'attr_names', is_list)
    assert all(is_str(n) for n in data['attr_names'])


def validate_claim_def_txn(txn):
    require(txn, 'type', is_one_of('102'))

    data = txn['data']
    require(data, 'data', is_dict)
    require(data['data'], 'primary', is_dict)
    require(data['data'], 'revocation', is_dict)
    require(data, 'ref', is_int)
    require(data, 'signature_type', is_one_of('CL'))
    optional(data, 'tag', is_str)


# Misc utility


def sdk_build_schema_request(looper, sdk_wallet_client,
                             attributes=[], name="", version=""):
    _, identifier = sdk_wallet_client

    _, schema_json = looper.loop.run_until_complete(
        issuer_create_schema(
            identifier, name,
            version, json.dumps(attributes)
        ))

    return looper.loop.run_until_complete(
        build_schema_request(identifier, schema_json)
    )


def build_get_rs_schema_request( did, txnId):
    identifier, type, name, version = txnId.split(':')
    #_id = identifier + ':' + type + ':' + name + ':' + version
    txn_dict = {
        'operation': {
            'type': "301",
            'from': identifier,
            'meta': {
                'name': name,
                'version': version,
                'type': 'sch' #type
            }
        },
        "identifier": did,
        "reqId": next(_reqId),
        "protocolVersion": 2
    }
    schema_json = json.dumps(txn_dict)
    return schema_json


def build_rs_schema_request( identifier, schema={}, name="", version=""):
    txn_dict = {
        'operation': {
            'type': "201",
            'meta': {
                'name': name,
                'version': version,
                'type': "sch"
            },
            'data': {
                'schema': schema
            }
        },
        "identifier": identifier,
        "reqId": next(_reqId),
        "protocolVersion": 2
    }
    schema_json = json.dumps(txn_dict)
    return schema_json


def sdk_write_schema(looper, sdk_pool_handle, sdk_wallet_client, multi_attribute=[], name="", version=""):
    _, identifier = sdk_wallet_client
    if multi_attribute:
        _, schema_json = looper.loop.run_until_complete(
            issuer_create_schema(identifier, name, version, json.dumps(multi_attribute)))
    else:
        _, schema_json = looper.loop.run_until_complete(
            issuer_create_schema(identifier, "name", "1.0", json.dumps(["first", "last"])))
    request = looper.loop.run_until_complete(build_schema_request(identifier, schema_json))
    return schema_json, \
        sdk_get_reply(looper, sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet_client, request))[1]


def sdk_write_schema_and_check(looper, sdk_pool_handle, sdk_wallet_client,
                               attributes=[], name="", version=""):
    request = sdk_build_schema_request(looper, sdk_wallet_client,
                                       attributes, name, version)
    req = sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet_client, request)
    rep = sdk_get_and_check_replies(looper, [req])
    return rep


def sdk_write_rich_schema(looper, sdk_pool_handle, sdk_wallet, request):
    return sdk_get_reply(looper, sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet, request))[1]


def sdk_write_rs_schema_and_check(looper, sdk_pool_handle, sdk_wallet, request):
    req = sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet, request)
    rep = sdk_get_and_check_replies(looper, [req])
    return rep


def sdk_write_context(looper, sdk_pool_handle, sdk_wallet_steward, context=[], name="", version=""):
    _wh, did = sdk_wallet_steward
    # create json
    raw_json = {
        'operation': {
            TXN_TYPE: SET_CONTEXT,
            META: {
                CONTEXT_NAME: name,
                CONTEXT_VERSION: version,
                RS_TYPE: CONTEXT_TYPE
            },
            DATA: context
        },
        "identifier": did,
        "reqId": 12345678,
        "protocolVersion": CURRENT_PROTOCOL_VERSION,
    }
    set_context_txn_json = json.dumps(raw_json)

    return json.dumps({'id': did + ':' + MARKER_CONTEXT + ':' + name + ':' + version}), \
        sdk_get_reply(looper, sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet_steward, set_context_txn_json))[1]


def sdk_write_context_and_check(looper, sdk_pool_handle, sdk_wallet_steward,
                                context=[], name="", version="", reqId=12345678):
    _wh, did = sdk_wallet_steward

    # create json
    raw_json = {
        'operation': {
            TXN_TYPE: SET_CONTEXT,
            META: {
                CONTEXT_NAME: name,
                CONTEXT_VERSION: version,
                RS_TYPE: CONTEXT_TYPE
            },
            DATA: context
        },
        "identifier": did,
        "reqId": reqId,
        "protocolVersion": CURRENT_PROTOCOL_VERSION,
    }
    set_context_txn_json = json.dumps(raw_json)
    req = sdk_sign_and_submit_req(sdk_pool_handle, sdk_wallet_steward, set_context_txn_json)
    rep = sdk_get_and_check_replies(looper, [req])
    return rep
