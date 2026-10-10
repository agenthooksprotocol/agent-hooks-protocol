const assert = require('node:assert/strict');
const sdk = require(require('node:path').resolve(process.argv[2]));
const input = {callId:'c',name:'read',input:{path:'app-owned'},path:'/tools/read',origin:'native',callSynthesized:false,parentEventId:'parent'};
const event = sdk.toEventInput('tool.before',input);
assert.deepEqual(event,{type:'tool.before',call:{id:'c',synthesized:false},tool:{name:'read',input:{path:'app-owned'},origin:'native'},path:'/tools/read',parentEventId:'parent'});
assert.deepEqual(input.input,{path:'app-owned'});
assert.equal('source' in event,false);
assert.equal('time' in event,false);
assert.deepEqual(sdk.state.initial(sdk.Permission.None),{permission:'none',candidate:null});
const candidate = sdk.state.candidate({answer:1},{producer:'hook'});
const initial = sdk.state.initial(sdk.Permission.Allow,{candidate,flow:'stop',instructions:['keep'],future:7});
assert.deepEqual(initial,{permission:'allow',candidate,flow:'stop',instructions:['keep'],future:7});
assert.deepEqual(sdk.effects.modify_input.replace({next:true}),{type:'modify',target:'input',operation:'replace',value:{next:true}});
assert.deepEqual(sdk.effects.deny('reason'),{type:'deny',reason:'reason'});
assert.deepEqual(sdk.effects.return({answer:1}),{type:'return',value:{answer:1}});
console.log('generated TypeScript ergonomics passed');
assert.equal(Object.keys(sdk.contentSlots).length, 32);
const source = { read() { throw new Error('binding must not read'); } };
assert.deepEqual(sdk.contentSlots['tool.before'].items(2, source), {path:['items',2],source});
assert.throws(() => sdk.contentSlots['tool.before'].items(-1, source), RangeError);
assert.equal(typeof sdk.contentSlots['context.compact.before'], 'object');

assert.equal(sdk.events.toolBefore, 'tool.before');
assert.equal(Object.keys(sdk.events).length, 32);

assert.equal(sdk.effectNames.deny, 'deny');
assert.equal(sdk.supports({effects: ['deny']}, sdk.effectNames.deny), true);
assert.equal(sdk.supports({effects: []}, sdk.effectNames.deny), false);
assert.equal(sdk.supports({effects: [], modify: {input: {replace: true}}}, sdk.effectNames.modify), false);
assert.equal(sdk.supports({effects: ['vendor.custom']}, 'vendor.custom'), true);
assert.equal(sdk.supports({effects: ['vendor.custom']}, 'vendor.other'), false);
assert.equal(sdk.supports(sdk.capabilities.intercept().deny().capabilities, sdk.effectNames.deny), true);
const incoming = sdk.parseInterceptRequest({jsonrpc:'2.0',id:0,method:'hooks/intercept',params:{protocolVersion:'draft',event:{type:'future'},capabilities:{effects:['deny','vendor.custom']},state:{permission:'none',candidate:null}}});
assert.equal(incoming.ok, true, JSON.stringify(incoming.diagnostics));
assert.equal(sdk.supports(incoming.value.params.capabilities, sdk.effectNames.deny), true);
assert.equal(sdk.supports(incoming.value.params.capabilities, 'vendor.custom'), true);

assert.equal(sdk.contentSlots['context.compact.before'].itemsParts, undefined);
assert.equal(sdk.contentSlots['context.compact.before'].instructions, undefined);
