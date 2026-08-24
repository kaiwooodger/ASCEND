/*
 * Optional localhost browser adapter for the ASCEND controller.
 * This file collects user configuration and renders stored API results. It
 * must not calculate or reinterpret scientific endpoints in the browser.
 */
let state={case:null};
const $=id=>document.getElementById(id);
const value=id=>$(id).value.trim();
const nullableNumber=id=>value(id)===""?null:Number(value(id));
const escapeHtml=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path,body){
  const options=body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)};
  const response=await fetch(path,options);const data=await response.json();
  if(!response.ok||data.ok===false)throw new Error(data.error||`Request failed: ${response.status}`);
  return data;
}
// Wrap each controller request in one busy/error/refresh lifecycle.
function setBusy(on){$("busy").classList.toggle("hidden",!on)}
async function action(fn){setBusy(true);try{const data=await fn();if(data?.case!==undefined)state=data;await refresh()}catch(error){$("message").textContent=`ERROR: ${error.message}`}finally{setBusy(false)}}

function showPage(name){document.querySelectorAll(".page").forEach(x=>x.classList.toggle("active",x.id===`page-${name}`));document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("active",x.dataset.page===name));if(name==="review")renderReview()}
document.querySelectorAll("nav button").forEach(button=>button.onclick=()=>showPage(button.dataset.page));

function configuration(){
  // Preserve configuration fields not exposed by this compact browser form;
  // the server performs authoritative validation and ROI-name migration.
  const fractions=nullableNumber("fractions");const roles={};
  if(value("mapGTV"))roles.GTV=value("mapGTV");if(value("mapTL"))roles.T_L=value("mapTL");if(value("mapVTVH"))roles.VTV_H=value("mapVTVH");if(value("mapVTVL"))roles.VTV_L=value("mapVTVL");
  if(value("mapIndividuals"))roles.VTV_H_individual=value("mapIndividuals").split(",").map(x=>x.trim()).filter(Boolean);
  const previous=state.case?.configuration||{};
  return {...previous,treatment_delivery_mode:value("mode"),dose_context:value("doseContext"),prescriptions:{Rx_L:{gy:nullableNumber("rxL"),fractions,source:value("rxLSource")},Rx_H:{gy:nullableNumber("rxH"),fractions,source:value("rxHSource")}},fractionation:fractions?{fractions}:{},structure_roles:roles,structure_bindings:previous.structure_bindings||{},validation_structures:previous.validation_structures||[]};
}

async function refresh(){state=await api("/api/state");const c=state.case;$("message").textContent=state.message||"Ready";if(!c){$("caseStatus").textContent="Case: — · Layer 1: NOT RUN · Layer 2.1: NOT RUN · Layer 2.2: NOT RUN";return}
  $("caseStatus").textContent=`Case: ${c.case_id} · Layer 1: ${c.layer1_status} · Layer 2.1: ${c.layer2_1.calculation_status} · Layer 2.2: ${c.layer2_2.calculation_status}`;
  $("inventory").textContent=JSON.stringify({case_id:c.case_id,detected_objects:Object.fromEntries(Object.entries(c.dicom_objects).map(([k,v])=>[k,v.length])),dicom_chains:c.dicom_chains,selected_chain_id:c.selected_chain_id,selected:c.selected_objects,warnings:c.warnings},null,2);
  $("chainSelect").innerHTML=(c.dicom_chains||[]).map(x=>`<option value="${escapeHtml(x.chain_id)}" ${x.chain_id===c.selected_chain_id?"selected":""}>${escapeHtml(x.chain_id)} · ${escapeHtml(x.validity_status)} · ${escapeHtml(x.display?.plan_label||"")}</option>`).join("");
  const config=c.configuration;$("mode").value=config.treatment_delivery_mode;$("doseContext").value=config.dose_context;$("rxL").value=config.prescriptions.Rx_L.gy??"";$("rxH").value=config.prescriptions.Rx_H.gy??"";$("rxLSource").value=config.prescriptions.Rx_L.source;$("rxHSource").value=config.prescriptions.Rx_H.source;$("fractions").value=config.fractionation.fractions??config.prescriptions.Rx_L.fractions??"";
  const roles=config.structure_roles;$("mapGTV").value=roles.GTV||"";$("mapTL").value=roles.T_L||"";$("mapVTVH").value=roles.VTV_H||"";$("mapVTVL").value=roles.VTV_L||"";$("mapIndividuals").value=Array.isArray(roles.VTV_H_individual)?roles.VTV_H_individual.join(", "):"";
  const selectedStruct=(c.dicom_objects.RTSTRUCT||[]).find(x=>x.path===c.selected_objects.rtstruct);const names=selectedStruct?.roi_names||[];$("roiNames").innerHTML=names.map(x=>`<option value="${escapeHtml(x)}">`).join("");renderReview();await renderResults();
}
async function renderResults(){if(!state.case)return;
  // Results are displayed from persisted API payloads without recalculation.
  const l1=await api("/api/result/layer1");$("layer1Result").textContent=JSON.stringify(l1.error?{error:l1.error}:l1.result?.findings||[],null,2);
  const l21=await api("/api/result/layer2_1");const metrics=l21.result?.harmonised_metrics||[];$("metrics").innerHTML=metrics.length?`<table><thead><tr><th>Metric</th><th>Value</th><th>Units</th><th>Applicability</th><th>Warnings</th></tr></thead><tbody>${metrics.map(m=>`<tr><td>${escapeHtml(m.metric_id)}</td><td>${escapeHtml(m.value)}</td><td>${escapeHtml(m.units)}</td><td>${escapeHtml(m.applicability)}</td><td>${escapeHtml((m.warnings||[]).join(", "))}</td></tr>`).join("")}</tbody></table>`:"";$("layer21Error").textContent=l21.error||"";
  const l22=await api("/api/result/layer2_2");$("layer22Result").textContent=JSON.stringify(l22.error?{error:l22.error}:{calculation_status:l22.result?.calculation_status,interpretation_status:l22.result?.interpretation_status,plan_ipvdr:l22.result?.plan_ipvdr,graph_summary:l22.result?.graph_summary,warnings:l22.result?.warnings},null,2);
  const l31=await api("/api/result/layer3_1");$("layer31Result").textContent=JSON.stringify(l31.error?{error:l31.error}:{calculation_status:l31.result?.calculation_status,fraction_history:l31.result?.fraction_history,layer3_1a:l31.result?.layer3_1a_conventional_lq,layer3_1b:l31.result?.layer3_1b_high_dose_sfrt_response,layer3_1c:l31.result?.layer3_1c_modelled_therapeutic_ratio},null,2);
  const l32=await api("/api/result/layer3_2");$("layer32Result").textContent=JSON.stringify(l32.error?{error:l32.error}:l32.result,null,2);
}
function renderReview(){$("reviewResult").textContent=JSON.stringify(state.case||{status:"No case"},null,2)}

$("browseSource").onclick=()=>action(async()=>{const r=await api("/api/choose/directory");if(r.path)$("sourceDirectory").value=r.path;return state});
$("browseCase").onclick=()=>action(async()=>{const r=await api("/api/choose/case_file");if(r.path)$("caseFile").value=r.path;return state});
$("importCase").onclick=()=>action(()=>api("/api/import",{source_directory:value("sourceDirectory")}));
$("openCase").onclick=()=>action(()=>api("/api/open",{case_file:value("caseFile")}));
$("selectChain").onclick=()=>action(()=>api("/api/select-chain",{chain_id:value("chainSelect"),allow_incomplete_chain:Boolean(value("chainOverrideReason")),override_reason:value("chainOverrideReason")||null}));
$("inspectCache").onclick=()=>action(async()=>{const r=await api("/api/cache/inspect",{});$("inventory").textContent=JSON.stringify(r.entries,null,2);return r});
$("clearCache").onclick=()=>{if(window.confirm("Remove all reusable Layer 1 cache entries for this case?"))action(()=>api("/api/cache/clear",{confirmed:true}))};
document.querySelectorAll(".save-config").forEach(button=>button.onclick=()=>action(()=>api("/api/configure",configuration())));
$("runLayer1").onclick=()=>action(async()=>{await api("/api/configure",configuration());return api("/api/run/layer1",{})});
$("runLayer21").onclick=()=>action(async()=>{await api("/api/configure",configuration());return api("/api/run/layer2_1",{});});
$("runLayer22").onclick=()=>action(async()=>{await api("/api/configure",configuration());return api("/api/run/layer2_2",{});});
$("runLayer31").onclick=()=>action(async()=>{await api("/api/configure",configuration());return api("/api/run/layer3_1",{});});
$("runLayer32").onclick=()=>action(async()=>{await api("/api/configure",configuration());return api("/api/run/layer3_2",{});});
$("exportCase").onclick=()=>action(async()=>{const r=await api("/api/export",{});$("exportResult").textContent=JSON.stringify(r.files,null,2);return r});
refresh();
