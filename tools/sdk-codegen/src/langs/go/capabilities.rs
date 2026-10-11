//! Event-specific capability construction. The wire codecs remain independent.
use super::*;

pub(super) fn emit(ir: &Ir) -> Result<String> {
    let events = crate::capability_ergonomics::catalogue(ir)?;
    if events.is_empty() {
        return Ok(String::new());
    }
    let mut out = String::from(
        r#"
// Observation is closed: only Observe explicitly grants observation.
type Observation interface { observation() }
type Observe struct{}
func (Observe) observation() {}
func observes(v Observation) bool { switch observation := v.(type) { case Observe: return true; case *Observe: return observation != nil }; return false }
// Modify has no public representation; its zero value grants nothing.
type Modify struct { replace, merge bool }
func Replace() Modify { return Modify{replace:true} }
func Merge() Modify { return Modify{merge:true} }
func ReplaceAndMerge() Modify { return Modify{replace:true,merge:true} }
func (v Modify) granted() bool { return v.replace || v.merge }
type WireModification struct { Replace bool `json:"replace"`; Merge bool `json:"merge"` }
func (v Modify) wire() WireModification { return WireModification{v.replace,v.merge} }
type WireFlow struct { Operations []string `json:"operations"`; RemainingContinuations *uint64 `json:"remainingContinuations,omitempty"`; ContinuationCount *uint64 `json:"continuationCount,omitempty"` }
type WireContextInjection struct { Append bool `json:"append"`; DeliverAt []string `json:"deliverAt"` }
type WireInjection struct { Context WireContextInjection `json:"context"` }
type WireCapabilities struct {
 Effects []string `json:"effects"`
 Modify map[string]WireModification `json:"modify,omitempty"`
 Flow *WireFlow `json:"flow,omitempty"`
 Inject *WireInjection `json:"inject,omitempty"`
 Elicitation map[string]struct{} `json:"elicitation,omitempty"`
}
type WireDeclaration struct { Modes []string `json:"modes"`; Capabilities *WireCapabilities `json:"capabilities,omitempty"` }
func addEffect(v *WireCapabilities, effect string) { for _, existing := range v.Effects { if existing == effect { return } }; v.Effects = append(v.Effects,effect) }
// Flow is closed. Stop-only event fields use concrete StopFlow.
type Flow interface { flowWire() *WireFlow }
type StopFlow struct { granted bool }
type ContinueFlow struct { granted bool; remaining,count uint64 }
type StopContinueFlow struct { continuation ContinueFlow }
func Stop() StopFlow { return StopFlow{granted:true} }
func Continue(remaining,count uint64) ContinueFlow { return ContinueFlow{granted:true,remaining:remaining,count:count} }
func ContinueAndStop(remaining,count uint64) StopContinueFlow { return StopContinueFlow{continuation:Continue(remaining,count)} }
func (v StopFlow) flowWire() *WireFlow { if !v.granted { return nil }; return &WireFlow{Operations:[]string{"stop"}} }
func (v ContinueFlow) flowWire() *WireFlow { if !v.granted { return nil }; remaining,count := v.remaining,v.count; return &WireFlow{Operations:[]string{"continue"},RemainingContinuations:&remaining,ContinuationCount:&count} }
func (v StopContinueFlow) flowWire() *WireFlow { w := v.continuation.flowWire(); if w!=nil { w.Operations = append(w.Operations,"stop") }; return w }
func renderFlow(v Flow) *WireFlow { switch grant := v.(type) {
case StopFlow: return grant.flowWire()
case ContinueFlow: return grant.flowWire()
case StopContinueFlow: return grant.flowWire()
case *StopFlow: if grant!=nil { return grant.flowWire() }
case *ContinueFlow: if grant!=nil { return grant.flowWire() }
case *StopContinueFlow: if grant!=nil { return grant.flowWire() }
}; return nil }
// Injection's private state always couples append with nonempty delivery timing.
type Injection struct { deliveries []string }
func (v Injection) with(delivery string) Injection { values := append([]string(nil),v.deliveries...); for _,existing := range values { if existing == delivery { return Injection{values} } }; return Injection{append(values,delivery)} }
func (v Injection) wire() *WireInjection { if len(v.deliveries)==0 { return nil }; return &WireInjection{Context:WireContextInjection{Append:true,DeliverAt:append([]string(nil),v.deliveries...)}} }
"#,
    );
    let deliveries = events
        .iter()
        .flat_map(|e| e.delivery.iter())
        .collect::<BTreeSet<_>>();
    for delivery in deliveries {
        let name = go_identifier(delivery);
        writeln!(
            out,
            "func Inject{name}() Injection {{ return Injection{{}}.with({delivery:?}) }}\nfunc (v Injection) With{name}() Injection {{ return v.with({delivery:?}) }}"
        )?;
    }
    let all_deliveries = events
        .iter()
        .flat_map(|e| e.delivery.iter().cloned())
        .collect::<BTreeSet<_>>();
    for event in events.iter().filter(|event| event.intercept) {
        for (target, operations) in &event.modify {
            anyhow::ensure!(
                operations
                    .iter()
                    .all(|operation| operation == "replace" || operation == "merge"),
                "unsupported Go modification operation"
            );
            if modify_type(&event.name, target, operations) != "Modify" {
                let ty = modify_type(&event.name, target, operations);
                writeln!(
                    out,
                    "type {ty} struct {{ value Modify }}\nfunc (v {ty}) granted() bool {{ return v.value.granted() }}\nfunc (v {ty}) wire() WireModification {{ return v.value.wire() }}"
                )?;
                for operation in operations {
                    let constructor = go_identifier(operation);
                    writeln!(
                        out,
                        "func {ty}{constructor}() {ty} {{ return {ty}{{value:{constructor}()}} }}"
                    )?;
                }
            }
        }
        if !event.delivery.is_empty()
            && event.delivery.iter().cloned().collect::<BTreeSet<_>>() != all_deliveries
        {
            let ty = format!("{}Injection", event.name);
            writeln!(
                out,
                "type {ty} struct {{ value Injection }}\nfunc (v {ty}) wire() *WireInjection {{ return v.value.wire() }}"
            )?;
            for delivery in &event.delivery {
                let name = go_identifier(delivery);
                writeln!(
                    out,
                    "func {}Inject{name}() {ty} {{ return {ty}{{value:Inject{name}()}} }}\nfunc (v {ty}) With{name}() {ty} {{ return {ty}{{value:v.value.with({delivery:?})}} }}",
                    event.name
                )?;
            }
        }
    }
    out.push_str("type Host struct {\n");
    for event in &events {
        writeln!(
            out,
            "{} {}",
            event.name,
            if event.intercept {
                format!("{}Declaration", event.name)
            } else {
                "Observation".to_string()
            }
        )?;
    }
    out.push_str("}\nfunc (v Host) Wire() map[string]WireDeclaration { out := map[string]WireDeclaration{}\n");
    for event in &events {
        if event.intercept {
            writeln!(
                out,
                "if declaration,ok := wire{}(v.{}); ok {{ out[{:?}] = declaration }}",
                event.name, event.name, event.event
            )?;
        } else if event.observe {
            writeln!(
                out,
                "if observes(v.{}) {{ out[{:?}] = WireDeclaration{{Modes:[]string{{\"observe\"}}}} }}",
                event.name, event.event
            )?;
        }
    }
    out.push_str("return out }\n");
    for event in events.iter().filter(|e| e.intercept) {
        let declaration = format!("{}Declaration", event.name);
        let marker = format!("is{}Declaration", event.name);
        writeln!(
            out,
            "type {declaration} interface {{ {marker}() }}\nfunc ({}) {marker}() {{}}",
            event.name
        )?;
        if event.observe {
            writeln!(out, "func (Observe) {marker}() {{}}")?;
        }
        writeln!(
            out,
            "func wire{}(value {declaration}) (WireDeclaration,bool) {{ switch v := value.(type) {{\ncase {}: return v.wire()\ncase *{}: if v != nil {{ return v.wire() }}",
            event.name, event.name, event.name
        )?;
        if event.observe {
            out.push_str("case Observe: return WireDeclaration{Modes:[]string{\"observe\"}},true\ncase *Observe: if v != nil { return WireDeclaration{Modes:[]string{\"observe\"}},true }\n");
        }
        out.push_str("}; return WireDeclaration{},false }\n");
        writeln!(out, "type {} struct {{", event.name)?;
        for effect in &event.effects {
            if !["modify", "flow", "inject"].contains(&effect.as_str()) {
                writeln!(out, "{} bool", control_name(effect))?;
            }
        }
        for (target, operations) in &event.modify {
            writeln!(
                out,
                "Modify{} {}",
                go_identifier(target),
                modify_type(&event.name, target, operations)
            )?;
        }
        if !event.flow.is_empty() {
            writeln!(out, "Flow {}", flow_type(&event.flow))?;
        }
        if !event.delivery.is_empty() {
            writeln!(
                out,
                "InjectContext {}",
                if event.delivery.iter().cloned().collect::<BTreeSet<_>>() == all_deliveries {
                    "Injection".to_string()
                } else {
                    format!("{}Injection", event.name)
                }
            )?;
        }
        for mode in &event.elicitation {
            writeln!(out, "Elicitation{} bool", go_identifier(mode))?;
        }
        if event.observe {
            out.push_str("Observe bool\n");
        }
        writeln!(
            out,
            "}}\nfunc (v {}) wire() (WireDeclaration,bool) {{ caps := &WireCapabilities{{Effects:[]string{{}}}}",
            event.name
        )?;
        for effect in &event.effects {
            if !["modify", "flow", "inject"].contains(&effect.as_str()) {
                writeln!(
                    out,
                    "if v.{} {{ addEffect(caps,{effect:?}) }}",
                    control_name(effect)
                )?;
            }
        }
        for target in event.modify.keys() {
            writeln!(
                out,
                "if v.Modify{}.granted() {{ if caps.Modify == nil {{ caps.Modify = map[string]WireModification{{}} }}; caps.Modify[{target:?}] = v.Modify{}.wire(); addEffect(caps,\"modify\") }}",
                go_identifier(target),
                go_identifier(target)
            )?;
        }
        if !event.flow.is_empty() {
            out.push_str("if flow := renderFlow(v.Flow); flow != nil { caps.Flow = flow; addEffect(caps,\"flow\") }\n");
        }
        if !event.delivery.is_empty() {
            out.push_str("if injection := v.InjectContext.wire(); injection != nil { caps.Inject = injection; addEffect(caps,\"inject\") }\n");
        }
        for mode in &event.elicitation {
            writeln!(
                out,
                "if v.Elicitation{} {{ if caps.Elicitation == nil {{ caps.Elicitation = map[string]struct{{}}{{}} }}; caps.Elicitation[{mode:?}] = struct{{}}{{}} }}",
                go_identifier(mode)
            )?;
        }
        out.push_str("declaration := WireDeclaration{Modes:[]string{}}; if len(caps.Effects)>0 || len(caps.Elicitation)>0 { declaration.Modes = append(declaration.Modes,\"intercept\"); declaration.Capabilities = caps }\n");
        if event.observe {
            out.push_str(
                "if v.Observe { declaration.Modes = append(declaration.Modes,\"observe\") }\n",
            );
        }
        out.push_str("return declaration,len(declaration.Modes)>0 }\n");
    }
    Ok(out)
}

fn control_name(effect: &str) -> String {
    if effect == "return" {
        "ReturnResult".into()
    } else {
        go_identifier(effect)
    }
}

fn flow_type(operations: &[String]) -> &str {
    if operations == ["stop"] {
        "StopFlow"
    } else if operations == ["continue"] {
        "ContinueFlow"
    } else {
        "Flow"
    }
}

fn modify_type(event: &str, target: &str, operations: &[String]) -> String {
    if operations == ["merge", "replace"] {
        "Modify".into()
    } else {
        format!("{event}Modify{}", go_identifier(target))
    }
}
