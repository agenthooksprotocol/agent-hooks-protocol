const assert = require('node:assert/strict');
const sdk = require(require('node:path').resolve(process.argv[2]));
let evaluations = 0;
const lazy = () => { evaluations++; throw Error('must not evaluate'); };
const eager = new Proxy({}, {get() { throw Error('must not inspect'); }, ownKeys() { throw Error('must not serialize'); }});
const first = sdk.ownedAttachment(lazy);
const second = sdk.ownedAttachment(eager);
assert.deepEqual(JSON.parse(JSON.stringify(first)), {});
assert.deepEqual(JSON.parse(JSON.stringify(second)), {});
const text = {kind:'text', text:'Summarize this'};
const attachment = {id:'a1', kind:'attachment', category:'images', mediaType:'image/png', body:first, selection:'body', synthesized:false};
const input = {trigger:'manual', items:[{role:'user', parts:[text, attachment]}, {id:'m2', role:'assistant', parts:[{kind:'attachment', mediaType:'audio/wav', body:second}]}]};
const result = sdk._projectHostInput('context.compact.before', input);
assert.equal(evaluations, 0);
assert.equal(result.bindings[0].source, lazy);
assert.equal(result.bindings[1].source, eager);
assert.deepEqual(result.bindings.map(b => b.path), [['items',0,'parts',1], ['items',1,'parts',0]]);
assert.deepEqual(result.pending, [{path:['items',0,'parts',1],selection:'body'}, {path:['items',1,'parts',0],selection:'body'}]);
assert.deepEqual(result.event.items[0].parts[1], {id:'a1', synthesized:false, kind:'attachment', category:'images', mediaType:'image/png', selection:'metadata'});
assert.equal(result.event.items[0].parts[0].text, 'Summarize this');
assert.equal(result.event.items[0].parts[0].selection, 'body');
assert.equal(result.event.items[0].parts[0].mediaType, 'text/plain');
assert.equal(result.event.items[0].synthesized, true);
assert.equal(result.event.items[1].id, 'm2');
assert.equal(result.event.items[1].parts[0].mediaType, 'audio/wav');
assert.equal('body' in result.event.items[0].parts[1], false);
assert.doesNotThrow(() => JSON.stringify(result.event));
assert.equal(input.items[0].id, undefined);
assert.equal(input.items[0].parts[1].body, first);
const repeated = sdk._projectHostInput('context.compact.before', input);
assert.deepEqual(repeated.event, result.event);
const distinct = sdk._projectHostInput('context.compact.before', {trigger:'manual', items:[{role:'user', parts:[{kind:'text', text:'Summarize this'}]}]});
assert.notEqual(distinct.event.items[0].parts[0].id, result.event.items[0].parts[0].id);
assert.deepEqual(distinct.bindings, []);
assert.deepEqual(distinct.pending, []);
const single = sdk._projectHostInput('tool.before', {callId:'c', name:'x', input:eager, path:'/x', origin:'native', items:[{kind:'attachment', mediaType:'image/png', body:first}]});
assert.equal(single.event.tool.input, eager);
assert.deepEqual(single.bindings[0].path, ['items',0]);
const changes = sdk._projectHostInput('file.changed', {changes:[{path:'/x',operation:'update',agentCaused:true,before:{kind:'text',text:'old'},after:{kind:'attachment',mediaType:'image/png',body:first}}]});
assert.deepEqual(changes.bindings[0].path, ['changes',0,'after']);
assert.equal(changes.event.changes[0].before.text, 'old');
assert.equal(changes.event.changes[0].path, '/x');
const wire = {id:'wire',kind:'attachment', mediaType:'image/png', selection:'body', body:{ref:'content-id'}};
assert.deepEqual(sdk._projectHostInput('context.compact.before', {trigger:'manual', items:[{id:'m',role:'user',parts:[wire]}]}).bindings, []);
function project(part) { return sdk._projectHostInput('context.compact.before', {trigger:'manual', items:[{role:'user',parts:[part]}]}); }
const parameterized = project({kind:'attachment', mediaType:'image/png; charset=x', body:first, future:{ordinary:['json']}});
assert.equal(parameterized.event.items[0].parts[0].mediaType, 'image/png; charset=x');
assert.deepEqual(parameterized.event.items[0].parts[0].future, {ordinary:['json']});
assert.equal(parameterized.bindings[0].source, lazy);
assert.doesNotThrow(() => project({kind:'attachment', mediaType:'image/png; name="a;b.png"', body:first}));
assert.deepEqual(project({kind:'text',text:'x',future:{ordinary:true}}).event.items[0].parts[0].future, {ordinary:true});
for (const malformed of [
 {kind:'text',text:'x',synthesized:false},
 {kind:'attachment',mediaType:'image/png',body:first,synthesized:false},
 {kind:'text',text:'x',future:first},
 {kind:'attachment',mediaType:'image/png',body:first,future:second},
 {kind:'attachment',mediaType:'image/png',selection:'metadata',id:'m',future:first},
 ...['body','gap','size','sha256'].map(field => ({kind:'text',text:'x',[field]:field === 'gap' ? {reason:'unexpected'} : 'unexpected'})),
 {kind:'attachment',mediaType:'image/png',selection:'body',gap:{}},
 {kind:'attachment',mediaType:'image/png',selection:'body',gap:{reason:4}},
 {kind:'text',mediaType:'text/plain',selection:'body',gap:{reason:'unavailable'},size:'bad'},
 {kind:'attachment',mediaType:'image/png',selection:'metadata',size:'bad'},
 {kind:'attachment',mediaType:'image/png',selection:'metadata',size:1.5},
 {kind:'attachment',mediaType:'image/png',selection:'metadata',size:-1},
 {kind:'attachment',mediaType:'image/png',selection:'metadata',sha256:2},
 {kind:'attachment',mediaType:'image/png',selection:'body',gap:{reason:''}},
 ...['text/plain; charset=x','application/json; charset=x','application/ld+json; charset=x','image/png; charset=','image/png; malformed','image/png; charset="unterminated'].map(mediaType => ({kind:'attachment',mediaType,body:first})),
]) assert.throws(() => project(malformed), TypeError);
assert.throws(() => sdk._projectHostInput('context.compact.before', {trigger:'manual',items:[{role:'user',parts:[],synthesized:false}]}), TypeError);
assert.doesNotThrow(() => project({id:'provided',kind:'text',text:'x',synthesized:false}));

for (const part of [first, {kind:'message'}, {kind:'text',text:'bad',body:first}, {kind:'attachment',mediaType:'image/png',body:lazy},
 {kind:'attachment',mediaType:'image/png',body:first,ref:'wrong'},
 ...['text/plain','application/json','application/ld+json','not-media',''].map(mediaType => ({kind:'attachment',mediaType,body:first})),
 ...['metadata','omit','invalid'].map(selection => ({kind:'attachment',mediaType:'image/png',body:first,selection})),
 {kind:'attachment',mediaType:'image/png',selection:'body'},
 {kind:'text',mediaType:'image/png',selection:'metadata',id:'x'},
 {kind:'attachment',mediaType:'image/png',body:first,id:''},
 {kind:'attachment',mediaType:'image/png',body:first,category:''},
]) assert.throws(() => project(part), TypeError);
for (const items of [{}, [first], [{role:'user',parts:{}}], [{role:'bogus',parts:[]}], [{role:'user',parts:new Array(1)}]]) {
 assert.throws(() => sdk._projectHostInput('context.compact.before', {trigger:'manual',items}), TypeError);
}
// Native/application payloads stay opaque: only schema content slots are walked.
const native = {provider:'test',event:'event', payload:eager};
const opaque = sdk._projectHostInput('tool.before', {callId:'c',name:'x',input:eager,path:'/x',origin:'native',native});
assert.equal(opaque.event.native, native);
assert.equal(opaque.event.tool.input, eager);
assert.deepEqual(opaque.bindings, []);
assert.equal(evaluations, 0);
assert.equal(sdk.contentSlots['context.compact.before'].itemsParts, undefined);
console.log('generated TypeScript owned host inputs passed');

assert.equal(sdk.projectHostInput, undefined); // conversion hook is runtime-internal
