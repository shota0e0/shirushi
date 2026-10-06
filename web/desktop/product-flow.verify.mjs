import assert from "node:assert/strict";
import { DesktopAdapter } from "../adapters/desktop-adapter.js";
import { inspectionUiOutcome, strictUiMark, validateAddResult, validateImageRecord, validateProductInspection } from "./product-flow.js";
let checks=0;
const check=(v,label)=>{assert.ok(v,label);checks++;};
const reject=(fn)=>{assert.throws(fn);checks++;};
const sha="a".repeat(64), outSha="b".repeat(64);
const mark={version:1,mode:"typed",typed:"My work",handwritten:{coordinateSpace:{width:480,height:220},strokes:[]}};
const source={sha256:sha,size:100,format:"PNG"};
const record={url:"data:image/png;base64,aGVsbG8=",name:"source.png",reference:"C:/work/source.png",local:true,sha256:sha,size:100};
const outputRecord={...record,name:"source_rights.png",reference:"C:/work/source_rights.png",sha256:outSha,size:200};
const add={operation:"add",result:"ADD_SUCCESS",developmentSigning:true,source,output:{path:outputRecord.reference,sha256:outSha,size:200},personalMark:mark};
function inspection(present=false, metadata=null) {
  return {contractVersion:2,operation:"limited_c2pa_cawg_inspection",result:"LIMITED_INSPECTION",completeness:"INCOMPLETE",
    checks:{c2pa:"INSPECTED",cawg:"INSPECTED",trustmark:"NOT_CHECKED"},inspection:{contract:"shirushi-limited-inspection",contractVersion:1,
      overall:"LIMITED_INSPECTION",reasonCode:"LIMITED_SCOPE",trustmark:"NOT_CHECKED",fullVerificationPerformed:false,successMotionEligible:false,source,
      c2pa:{state:"INSPECTED",presence:present?"PRESENT":"ABSENT",parse:present,assertionDigestsValid:present,assetBindingValid:present,signature:present?"PREVIEW":"ABSENT",trustValidated:false},
      cawg:{state:"INSPECTED",presence:present?"PRESENT":"ABSENT",aiTrainingUse:present?"NOT_WANTED":"UNKNOWN",aiInferenceUse:present?"NOT_WANTED":"UNKNOWN"},personalMark:metadata}};
}
check(validateImageRecord(record).reference===record.reference,"native selected path retained");
reject(()=>validateImageRecord({...record,size:32*1024*1024+1}));
check(validateImageRecord({...record,size:32*1024*1024+1},{maximum:64*1024*1024}).size>32*1024*1024,"generated output has metadata allowance above input ceiling");
reject(()=>validateImageRecord({...record,size:64*1024*1024+1},{maximum:64*1024*1024}));
check(validateAddResult(add,mark).output.path!==record.reference,"separate output");
check(strictUiMark(mark).typed===mark.typed,"current typed view model");
const handwriting={...mark,mode:"handwritten",typed:"",handwritten:{coordinateSpace:{width:480,height:220},strokes:[[{x:.2,y:.3},{x:.8,y:.7}]]}};
check(strictUiMark(handwriting).handwritten.strokes.length===1,"handwritten geometry metadata");
check(validateProductInspection(inspection()).inspection.c2pa.presence==="ABSENT","unmarked absent");
check(!inspectionUiOutcome(inspection()).intentPresent,"unmarked no false intent");
check(inspectionUiOutcome(inspection(true,mark)).status==="LIMITED_INSPECTION","never VERIFIED");
check(inspectionUiOutcome(inspection(true,null)).mark===null,"rights without personal metadata not fabricated");
check(inspectionUiOutcome(inspection(true,mark)).result.inspection.successMotionEligible===false,"no full inspection success motion");
for(const mutate of [v=>v.result="VERIFIED",v=>v.completeness="COMPLETE",v=>v.checks.trustmark="INSPECTED",
  v=>v.inspection.successMotionEligible=true,v=>v.inspection.c2pa.trustValidated=true,v=>v.extra=true,
  v=>v.inspection.source.sha256="invalid",v=>v.inspection.c2pa.presence="ABSENT"]) {
  const v=structuredClone(inspection(true,mark));mutate(v);reject(()=>validateProductInspection(v));
}
reject(()=>validateProductInspection({...inspection(),inspection:{...inspection().inspection,personalMark:mark}}));
reject(()=>strictUiMark({...mark,glow:true}));
reject(()=>strictUiMark({...mark,typed:" "+mark.typed}));
reject(()=>validateAddResult({...add,result:"ADD_STAGED"},mark));
reject(()=>validateAddResult({...add,personalMark:handwriting},mark));
reject(()=>validateAddResult({...add,output:{...add.output,path:"../source.png"}},mark));
reject(()=>validateImageRecord({...record,reference:"\\\\server\\image.png"}));
reject(()=>validateImageRecord({...record,url:"https://example.com/image.png"}));
const calls=[];
let next=add, selectedRecord=record;
const transport={getCapabilities:async()=>{},loadPersonalMark:async()=>{},selectImage:async()=>selectedRecord,
  readImage:async(path)=>{calls.push(["read",path]);return outputRecord;},
  productOperation:async(request)=>{calls.push(request);return next;}};
const adapter=new DesktopAdapter(transport,{productFlow:true});
check(adapter.capabilities.coreAdd&&adapter.capabilities.coreVerify&&adapter.capabilities.sessionMarkEdit,"explicit product path enabled");
check(adapter.loadSessionMark()===null,"no fixture mark seeded");
await adapter.selectImageFromNative();
selectedRecord=null;
check(await adapter.selectImageFromNative()===null,"cancelled native replacement leaves current target intact");
adapter.saveSessionMark(handwriting);
check(adapter.loadSessionMark().mode==="handwritten","current session metadata no registry writes");
const result=await adapter.addMark({targetReference:record.reference,mark});
check(result.status==="SUCCESS"&&result.outputImage.reference===outputRecord.reference,"actual result consumes generated output");
check(calls[0].operation==="add"&&calls[0].inputPath===record.reference,"explicit add request exact selected input");
check(calls[0].expectedSource.sha256===sha&&calls[0].expectedSource.size===100,"selected image identity bound before native launch");
next=inspection(true,mark);next.inspection.source={sha256:outSha,size:200,format:"PNG"};
const verified=await adapter.verifyFileMark({targetReference:outputRecord.reference});
check(verified.status==="LIMITED_INSPECTION"&&verified.intentPresent,"real verify uses limited result");
await assert.rejects(()=>adapter.addMark({targetReference:record.reference,mark}));checks++;
check(calls.filter(c=>c.operation==="add").length===1,"old target cannot replay writer");
next={operation:"add",result:"ADD_FAILED",errorCode:"INPUT_UNAVAILABLE"};
await assert.rejects(()=>adapter.addMark({targetReference:outputRecord.reference,mark}));checks++;
for (const operation of ["add", "limited_inspect"]) {
  let consumed = 0;
  const entry = new DesktopAdapter({...transport, takeExplorerRequest:async()=>{
    consumed++; return {operation,image:record};
  }},{productFlow:true});
  check((await entry.takeExplorerRequest()).image.reference === record.reference,"Explorer preserves exact validated image identity");
  check(await entry.takeExplorerRequest() === null && consumed === 1,"Explorer request consumed once");
  next = operation === "add" ? add : inspection();
  const outcome = operation === "add" ? await entry.addMark({targetReference:record.reference,mark})
    : await entry.verifyFileMark({targetReference:record.reference});
  check(operation === "add" ? outcome.status === "SUCCESS" : !outcome.intentPresent,"Explorer uses existing Add/real absent verification contract");
}
for (const value of [{operation:"verify",image:record},{operation:"add",image:record,extra:true},
  {operation:"add",image:{...record,reference:"../bad.png"}}]) {
  let consumed = 0;
  const entry = new DesktopAdapter({...transport,takeExplorerRequest:async()=>{consumed++;return value;}},{productFlow:true});
  await assert.rejects(()=>entry.takeExplorerRequest()); checks++;
  check(await entry.takeExplorerRequest() === null && consumed === 1,"malformed handoff cannot retry");
}
for (const operation of ["add", "limited_inspect"]) {
  const value = {...record,size:64*1024*1024};
  const entry = new DesktopAdapter({...transport,takeExplorerRequest:async()=>({operation,image:value})},{productFlow:true});
  if (operation === "add") { await assert.rejects(()=>entry.takeExplorerRequest()); checks++; }
  else check((await entry.takeExplorerRequest()).image.size === value.size,"Explorer Verify preserves existing generated-image 64MiB limit");
  const overflow = new DesktopAdapter({...transport,takeExplorerRequest:async()=>({operation,image:{...value,size:value.size+1}})},{productFlow:true});
  await assert.rejects(()=>overflow.takeExplorerRequest());checks++;
}
console.log(`Product Flow: ${checks} checks PASS (synthetic transport only; no native execution)`);
