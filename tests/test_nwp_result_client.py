# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Public client contracts; real mTLS/server qualification belongs to demo integration."""
import asyncio
import copy
import json
import uuid

import httpx
import pytest

from nps_sdk.nwp.results import ResultHttpClient, ResultHttpError, ResultDescriptor

BASE = 'https://node.test/gateway'
ISSUER = 'urn:nps:node:demo:results'
REQUIRED = 'nps.data_results=1'
ACTIONS = ('result.create', 'result.read', 'result.aggregate', 'result.release')


def descriptor():
    return dict(result_ref='r'*64, issuer_nid=ISSUER, snapshot_id='s'*64,
                schema_ref='sha256:'+'a'*64, row_count=100000, created_at=1000, expires_at=1300, retention='process')


def manifest():
    return dict(node_id=ISSUER, node_type='action', manifest_version=1,
                auth=dict(required=True, identity_type='nip-cert', required_capabilities=['nwp:action']),
                actions={name: dict(required_capability='nwp:action') for name in ACTIONS},
                endpoints=dict(invoke='/gateway/invoke'), profiles=dict(feature_negotiation=dict(version=1,
                    feature_offer=[dict(id='nps.data_results', revisions=[dict(revision=1, lifecycle='experimental')])])),
                metadata=dict(optional_float=0.5))


def discovery(value=None, *, raw=None, headers=None):
    return httpx.Response(200, content=raw if raw is not None else json.dumps(value or manifest()).encode(),
        headers=headers or {'Content-Type':'application/nwp-manifest+json','X-NWM-Version':'1'})


def capsule(value, *, headers=None):
    return httpx.Response(200, content=json.dumps(dict(count=1, data=[value]), separators=(',', ':')).encode(), headers=headers or {
        'Content-Type':'application/nwp-capsule','X-NPS-Selected-Features':REQUIRED})


@pytest.mark.asyncio
async def test_four_public_operations_bind_refs_and_discover_each_time():
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method == 'GET': return discovery()
        assert request.headers['X-NPS-Required-Features'] == REQUIRED
        assert 'X-NPS-Invocation-Context' not in request.headers
        frame=json.loads(request.content)
        op=frame['action_id']
        if op=='result.create':return capsule(descriptor())
        assert frame['params']['result_ref']==descriptor()['result_ref']
        if op=='result.read':return capsule(dict(descriptor=descriptor(),rows=[dict(id='order:000000')],next_cursor=None))
        if op=='result.aggregate':return capsule(dict(descriptor=descriptor(),groups=[dict(keys=['delivery'],count=10,sum=12300)]))
        return capsule(dict(result_ref=descriptor()['result_ref'],released=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with ResultHttpClient(BASE,ISSUER,http_client=http) as client:
            key=uuid.uuid4();result=await client.create('orders',key,query={'filter':{'refunded':True}},ttl_seconds=300)
            page=await client.read(result,5,fields=['id']);assert page.rows[0]['id']=='order:000000'
            aggregate=await client.aggregate(result,['reason'],sum_field='amount_minor');assert aggregate.groups[0]['sum']==12300
            await client.release(result)
        assert not http.is_closed  # Supplied transport ownership remains with the caller.
    assert [request.method for request in seen]==['GET','POST']*4
    assert json.loads(seen[1].content)['idempotency_key']==str(key)
    assert json.loads(seen[5].content)['params']['aggregate']['operations']==[
        {'func':'COUNT','alias':'count'},{'func':'SUM','field':'amount_minor','alias':'sum'}]


@pytest.mark.parametrize('fault', ['missing-profile','wrong-issuer','auth-disabled','capability-string','wrong-capability',
    'wrong-route','old-feature','float-feature','bool-feature','duplicate-id','deprecated','float-version','bool-version',
    'wrong-version-header','bad-timestamp','role','duplicate-json','too-large','redirect','wrong-content-type',
    'invalid-feature-id','long-feature-id','unordered-revisions','too-many-features','overflow-revision'])
@pytest.mark.asyncio
async def test_discovery_fault_has_zero_post(fault):
    value=manifest();posts=0
    profile=value['profiles']['feature_negotiation'];revision=profile['feature_offer'][0]['revisions'][0]
    if fault=='missing-profile':value.pop('profiles')
    elif fault=='wrong-issuer':value['node_id']='urn:nps:node:other'
    elif fault=='auth-disabled':value['auth']['required']=False
    elif fault=='capability-string':value['auth']['required_capabilities']='nwp:action'
    elif fault=='wrong-capability':value['actions']['result.create']['required_capability']='nwp:other'
    elif fault=='wrong-route':value['endpoints']['invoke']='https://other.test/invoke'
    elif fault=='old-feature':revision['revision']=2
    elif fault=='float-feature':revision['revision']=1.0
    elif fault=='bool-feature':revision['revision']=True
    elif fault=='duplicate-id':profile['feature_offer']*=2
    elif fault=='deprecated':revision['lifecycle']='deprecated'
    elif fault=='float-version':value['manifest_version']=1.0
    elif fault=='bool-version':profile['version']=True
    elif fault=='bad-timestamp':value['manifest_updated_at']='not-a-timestamp'
    elif fault=='role':value['node_type']='memory'
    elif fault=='invalid-feature-id':profile['feature_offer'].append(dict(id='Bad-ID',revisions=[dict(revision=1,lifecycle='stable')]))
    elif fault=='long-feature-id':profile['feature_offer'].append(dict(id='nps.'+'a'*93,revisions=[dict(revision=1,lifecycle='stable')]))
    elif fault=='unordered-revisions':profile['feature_offer'][0]['revisions']=[dict(revision=2,lifecycle='stable'),dict(revision=1,lifecycle='stable')]
    elif fault=='too-many-features':profile['feature_offer'] += [dict(id=f'nps.extra_{index}',revisions=[dict(revision=1,lifecycle='stable')]) for index in range(32)]
    elif fault=='overflow-revision':profile['feature_offer'].append(dict(id='nps.extra',revisions=[dict(revision=65536,lifecycle='stable')]))
    def handler(request):
        nonlocal posts
        if request.method=='POST':posts+=1;return capsule(descriptor())
        if fault=='duplicate-json':return discovery(raw=json.dumps(value).replace('"manifest_version": 1','"manifest_version": 1,"manifest_version":1').encode())
        if fault=='too-large':return discovery(raw=b' '*65537)
        if fault=='redirect':return httpx.Response(307,headers={'Location':'https://other.test/.nwm'})
        if fault=='wrong-content-type':return discovery(value,headers={'Content-Type':'application/json','X-NWM-Version':'1'})
        if fault=='wrong-version-header':return discovery(value,headers={'Content-Type':'application/nwp-manifest+json','X-NWM-Version':'2'})
        return discovery(value)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as http:
        client=ResultHttpClient(BASE,ISSUER,http_client=http)
        with pytest.raises(ResultHttpError) as error:await client.create('orders',uuid.uuid4())
        assert not error.value.outcome_unknown
    assert posts==0


@pytest.mark.parametrize('fault', ['missing-echo','wrong-echo','wrong-content-type','count-bool','data-two','duplicate-json',
    'oversize','float-integer','unsafe-integer','surrogate','redirect','transport'])
@pytest.mark.asyncio
async def test_dispatched_protocol_or_transport_fault_is_uncertain_no_retry(fault):
    posts=0
    def handler(request):
        nonlocal posts
        if request.method=='GET':return discovery()
        posts+=1;value=descriptor()
        if fault=='transport':raise httpx.ReadTimeout('private transport detail')
        if fault=='redirect':return httpx.Response(307,headers={'Location':'https://other.test/invoke'})
        if fault=='missing-echo':return capsule(value,headers={'Content-Type':'application/nwp-capsule'})
        if fault=='wrong-echo':return capsule(value,headers={'Content-Type':'application/nwp-capsule','X-NPS-Selected-Features':'nps.data_results=2'})
        if fault=='wrong-content-type':return capsule(value,headers={'Content-Type':'application/json','X-NPS-Selected-Features':REQUIRED})
        if fault=='float-integer':value['row_count']=1.0
        if fault=='unsafe-integer':value['created_at']=9007199254740992
        if fault=='surrogate':value['result_ref']='\ud800'
        response=capsule(value)
        if fault=='count-bool':response=httpx.Response(200,json=dict(count=True,data=[value]),headers=response.headers)
        if fault=='data-two':response=httpx.Response(200,json=dict(count=1,data=[value,value]),headers=response.headers)
        if fault=='duplicate-json':response=httpx.Response(200,content=response.content.replace(b'"count":1',b'"count":1,"count":1'),headers=response.headers)
        if fault=='oversize':response=httpx.Response(200,content=b' '*131073,headers=response.headers)
        return response
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as http:
        client=ResultHttpClient(BASE,ISSUER,http_client=http)
        with pytest.raises(ResultHttpError) as error:await client.create('orders',uuid.uuid4())
        assert error.value.outcome_unknown
        assert 'private transport detail' not in str(error.value)
    assert posts==1


@pytest.mark.parametrize('status,uncertain',[(403,False),(503,True)])
@pytest.mark.asyncio
async def test_structured_errors_keep_bounded_status_and_no_success_echo(status,uncertain):
    def handler(request):
        if request.method=='GET':return discovery()
        return httpx.Response(status,json={'status':'NPS-AUTH-FORBIDDEN','error':'NWP-RESULT-ACCESS-DENIED'},
            headers={'Content-Type':'application/nwp-error+json'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ResultHttpError) as error:await ResultHttpClient(BASE,ISSUER,http_client=http).create('orders',uuid.uuid4())
        assert error.value.outcome_unknown is uncertain
        assert error.value.http_status==status and error.value.error_code=='NWP-RESULT-ACCESS-DENIED'


@pytest.mark.asyncio
async def test_manifest_rollback_or_same_version_change_rejects_before_second_post():
    for rollback in (False,True):
        gets=0;posts=0
        def handler(request):
            nonlocal gets,posts
            if request.method=='POST':posts+=1;return capsule(descriptor())
            gets+=1;value=manifest();value['manifest_version']=2 if gets==1 else (1 if rollback else 2)
            if gets==2:value['metadata']['changed']=True
            return discovery(value,headers={'Content-Type':'application/nwp-manifest+json','X-NWM-Version':str(value['manifest_version'])})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client=ResultHttpClient(BASE,ISSUER,http_client=http)
            await client.create('orders',uuid.uuid4())
            with pytest.raises(ResultHttpError) as error:await client.create('orders',uuid.uuid4())
            assert not error.value.outcome_unknown
        assert posts==1


@pytest.mark.asyncio
async def test_rebound_page_and_raw_payload_whitespace_limit_reject():
    for oversize in (False,True):
        def handler(request):
            if request.method=='GET':return discovery()
            value=dict(descriptor=descriptor(),rows=[],next_cursor=None)
            if not oversize:value['descriptor']['snapshot_id']='different-snapshot'
            raw=json.dumps({'count':1,'data':[value]}).encode()
            if oversize:raw=raw.replace(b'"rows": []',b'"rows": [ '+b' '*65536+b']')
            return httpx.Response(200,content=raw,headers={'Content-Type':'application/nwp-capsule','X-NPS-Selected-Features':REQUIRED})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client=ResultHttpClient(BASE,ISSUER,http_client=http)
            with pytest.raises(ResultHttpError):await client.read(ResultDescriptor(**descriptor()),5)


@pytest.mark.asyncio
async def test_invalid_local_parameters_never_contact_transport():
    calls=0
    def handler(_):
        nonlocal calls;calls+=1;return discovery()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=ResultHttpClient(BASE,ISSUER,http_client=http)
        for key,query in ((uuid.uuid1(),None),(uuid.uuid4(),{'sql':'select *'}),(uuid.uuid4(),{'filter':{'amount_minor':1.0}}),
                          (uuid.uuid4(),{'filter':{'amount_minor':9007199254740992}})):
            with pytest.raises(ValueError):await client.create('orders',key,query=query)
        for limit in (0,1001,True):
            with pytest.raises(ValueError):await client.read(ResultDescriptor(**descriptor()),limit)
        with pytest.raises(ValueError):await client.aggregate(ResultDescriptor(**descriptor()),['count'])
    assert calls==0


@pytest.mark.asyncio
async def test_cancellation_propagates_without_automatic_retry_or_rollback_claim():
    posts=0
    async def handler(request):
        nonlocal posts
        if request.method=='GET':return discovery()
        posts+=1;raise asyncio.CancelledError()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(asyncio.CancelledError):await ResultHttpClient(BASE,ISSUER,http_client=http).create('orders',uuid.uuid4())
    assert posts==1


@pytest.mark.parametrize('url', ['http://node.test', 'https://user:password@node.test', 'https://node.test?query=1',
    'https://node.test/#fragment', 'https://node.test:bad', 'https://node.test:99999'])
def test_host_configuration_rejects_unpinned_routes(url):
    with pytest.raises(ValueError):ResultHttpClient(url,ISSUER)


@pytest.mark.asyncio
async def test_local_scope_and_collection_validation_cannot_broaden_request():
    calls=0
    def handler(_):
        nonlocal calls;calls+=1;return discovery()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=ResultHttpClient(BASE,ISSUER,http_client=http)
        bad=descriptor();bad['issuer_nid']='urn:nps:node:other'
        with pytest.raises(ResultHttpError):await client.read(ResultDescriptor(**bad),1)
        for fields in ('id',['id','id'],['']):
            with pytest.raises(ValueError):await client.read(ResultDescriptor(**descriptor()),1,fields=fields)
        for ttl in (0,901,True):
            with pytest.raises(ValueError):await client.create('orders',uuid.uuid4(),ttl_seconds=ttl)
        with pytest.raises(ValueError):await client.aggregate(ResultDescriptor(**descriptor()),'reason')
        deep={};child=deep
        for _ in range(65):child['nested']={};child=child['nested']
        with pytest.raises(ValueError):await client.create('orders',uuid.uuid4(),query={'filter':deep})
    assert calls==0


@pytest.mark.parametrize('operation,fault', [('read','rows'),('read','cursor'),('aggregate','groups'),('release','false')])
@pytest.mark.asyncio
async def test_malformed_operation_outputs_cannot_become_inspected_evidence(operation,fault):
    def handler(request):
        if request.method=='GET':return discovery()
        if operation=='read':return capsule(dict(descriptor=descriptor(),rows=0 if fault=='rows' else [],next_cursor=True if fault=='cursor' else None))
        if operation=='aggregate':return capsule(dict(descriptor=descriptor(),groups='not-groups'))
        return capsule(dict(result_ref=descriptor()['result_ref'],released=False))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=ResultHttpClient(BASE,ISSUER,http_client=http);value=ResultDescriptor(**descriptor())
        with pytest.raises(ResultHttpError):
            if operation=='read':await client.read(value,1)
            elif operation=='aggregate':await client.aggregate(value,[])
            else:await client.release(value)
