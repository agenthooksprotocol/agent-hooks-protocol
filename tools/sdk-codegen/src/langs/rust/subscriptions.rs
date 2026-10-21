//! Language-native subscription conveniences layered over the canonical models.
use super::*;

fn has(ir: &Ir, name: &str) -> bool {
    ir.types.iter().any(|named| named.name == name)
}

pub(super) fn module_exports(ir: &Ir, module: &str) -> String {
    if !has(ir, "InterceptSubscription") || boundary_inventory(ir).is_empty() {
        return String::new();
    }
    match module {
        "subscription" => {
            ", intercept, observe, InterceptBuilder, ObserveBuilder, Unset, InvalidTimeout".into()
        }
        "content" => ", ContentSelection as Selection".into(),
        "event" => ", EventType, subscription_events::*".into(),
        _ => String::new(),
    }
}

pub(super) fn emit(ir: &Ir, out: &mut String) -> Result<()> {
    if has(ir, "Configuration") {
        out.push_str(
            r#"
impl Configuration {
    /// Append a subscription, retaining insertion order.
    pub fn subscribe(mut self, subscription: impl Into<BackendSubscriptionsItem>) -> Self {
        self.subscriptions.push(subscription.into());
        self
    }
}
"#,
        );
    }
    if let Some(backend) = ir.types.iter().find(|named| named.name == "Backend") {
        if crate::ergonomics::object_fields(ir, &backend.shape).is_some_and(|fields| {
            fields
                .iter()
                .any(|f| f.wire_name == "subscriptions" && !f.required)
        }) {
            out.push_str(r#"
impl Backend {
    /// Construct a backend whose subscriptions are obtained through configuration discovery.
    pub fn without_subscriptions(id: impl Into<Box<ReverseDnsName>>, transport: impl Into<BackendTransport>) -> Self {
        let mut backend = Self::new(id, Vec::<BackendSubscriptionsItem>::new(), transport);
        backend.subscriptions = Presence::Missing;
        backend
    }
}
"#);
        }
    }
    if !has(ir, "InterceptSubscription") || boundary_inventory(ir).is_empty() {
        return Ok(());
    }
    emit_selectors(ir, out)?;
    out.push_str(r#"
impl ContentSelection {
    /// Select descriptors without content bodies for every category.
    pub fn metadata() -> Self { Self::new(ContentSelectionDefault::Metadata) }
}
/// Timeout must be positive and an exact number of milliseconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidTimeout;
impl std::fmt::Display for InvalidTimeout {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str("subscription timeout must be positive whole milliseconds") }
}
impl std::error::Error for InvalidTimeout {}
/// A required builder argument that has not yet been supplied.
#[derive(Debug, Clone, Copy)]
pub struct Unset;
/// An interception subscription. Only a fully specified builder can be subscribed.
/// ```compile_fail
/// use agenthooksprotocol::{Configuration, event, subscription};
/// Configuration::new().subscribe(subscription::intercept(event::ToolBefore));
/// ```
#[derive(Debug, Clone)]
pub struct InterceptBuilder<T = Unset, F = Unset, C = Unset> {
    events: Vec<String>,
    timeout: T,
    failure: F,
    content: C,
}
/// Start an interception subscription; no delivery policy is defaulted.
pub fn intercept(events: impl subscription_events::InterceptSelector) -> InterceptBuilder {
    InterceptBuilder { events: events.names(), timeout: Unset, failure: Unset, content: Unset }
}
impl<T, F, C> InterceptBuilder<T, F, C> {
    /// Set a positive whole-millisecond timeout. Invalid durations are retained as
    /// schema-invalid timeoutMs=0 for setup validation; never rounded or truncated.
    /// Use try_timeout to report invalid durations immediately.
    pub fn timeout(self, duration: std::time::Duration) -> InterceptBuilder<Integer, F, C> {
        let millis = if duration.is_zero() || duration.subsec_nanos() % 1_000_000 != 0 { 0 } else { duration.as_millis() };
        let timeout = serde_json::from_str(&millis.to_string()).expect("milliseconds are an integer");
        InterceptBuilder { events: self.events, timeout, failure: self.failure, content: self.content }
    }
    pub fn try_timeout(self, duration: std::time::Duration) -> Result<InterceptBuilder<Integer, F, C>, InvalidTimeout> {
        if duration.is_zero() || duration.subsec_nanos() % 1_000_000 != 0 { return Err(InvalidTimeout); }
        Ok(self.timeout(duration))
    }
    pub fn fail_open(self) -> InterceptBuilder<T, InterceptSubscriptionFailurePolicy, C> {
        InterceptBuilder { events: self.events, timeout: self.timeout, failure: InterceptSubscriptionFailurePolicy::FailOpen, content: self.content }
    }
    pub fn fail_closed(self) -> InterceptBuilder<T, InterceptSubscriptionFailurePolicy, C> {
        InterceptBuilder { events: self.events, timeout: self.timeout, failure: InterceptSubscriptionFailurePolicy::FailClosed, content: self.content }
    }
    pub fn content(self, content: ContentSelection) -> InterceptBuilder<T, F, ContentSelection> {
        InterceptBuilder { events: self.events, timeout: self.timeout, failure: self.failure, content }
    }
}
impl From<InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>> for InterceptSubscription {
    fn from(value: InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>) -> Self {
        Self::new(value.content, value.events.into_iter().map(InterceptSubscriptionEventsItem::Custom).collect::<Vec<_>>(), value.failure, value.timeout)
    }
}
impl From<InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>> for BackendSubscriptionsItem {
    fn from(value: InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>) -> Self {
        InterceptSubscription::from(value).into()
    }
}
#[derive(Debug, Clone)]
pub struct ObserveBuilder<C = Unset> { events: Vec<String>, content: C }
/// Start an observation subscription; content selection must be explicit.
pub fn observe(events: impl subscription_events::Selector) -> ObserveBuilder { ObserveBuilder { events: events.names(), content: Unset } }
impl<C> ObserveBuilder<C> {
    pub fn content(self, content: ContentSelection) -> ObserveBuilder<ContentSelection> {
        ObserveBuilder { events: self.events, content }
    }
}
impl From<ObserveBuilder<ContentSelection>> for ObserveSubscription {
    fn from(value: ObserveBuilder<ContentSelection>) -> Self {
        Self::new(value.content, value.events.into_iter().map(ObserveSubscriptionEventsItem::Custom).collect::<Vec<_>>())
    }
}
impl From<ObserveBuilder<ContentSelection>> for BackendSubscriptionsItem {
    fn from(value: ObserveBuilder<ContentSelection>) -> Self { ObserveSubscription::from(value).into() }
}

"#);
    Ok(())
}

fn emit_selectors(ir: &Ir, out: &mut String) -> Result<()> {
    let events = boundary_inventory(ir);
    out.push_str(
        r#"
/// Canonical typed subscription selectors. EventType remains the runtime event enum.
pub mod subscription_events {
    mod sealed { pub trait Sealed {} }
    pub trait Selector: sealed::Sealed { fn names(self) -> Vec<String>; }
    pub trait InterceptSelector: Selector {}
    #[derive(Debug, Clone, Copy)]
    pub struct Selection<S>(S);
    pub fn select<S: Selector>(selectors: S) -> Selection<S> { Selection(selectors) }
    impl<S: Selector> sealed::Sealed for Selection<S> {}
    impl<S: Selector> Selector for Selection<S> { fn names(self) -> Vec<String> { self.0.names() } }
    impl<S: InterceptSelector> InterceptSelector for Selection<S> {}
"#,
    );
    for (event, modes) in &events {
        let name = type_identifier(event);
        writeln!(
            out,
            "#[derive(Debug, Clone, Copy)] pub struct {name};
impl sealed::Sealed for {name} {{}}
impl Selector for {name} {{ fn names(self) -> Vec<String> {{ vec![{event:?}.into()] }} }}"
        )?;
        if modes.contains("intercept") {
            writeln!(out, "impl InterceptSelector for {name} {{}}")?;
        }
    }
    out.push_str(
        "#[derive(Debug, Clone, Copy)] pub struct All; pub const ALL: All = All;
impl sealed::Sealed for All {}
impl Selector for All { fn names(self) -> Vec<String> { vec![\"*\".into()] } }
impl InterceptSelector for All {}
",
    );
    let families = events
        .keys()
        .filter_map(|event| event.split_once('.').map(|(family, _)| family))
        .collect::<BTreeSet<_>>();
    for family in families {
        let module = snake_identifier(family);
        writeln!(
            out,
            "pub mod {module} {{
#[derive(Debug, Clone, Copy)] pub struct All; pub const ALL: All = All;
impl super::sealed::Sealed for All {{}}
impl super::Selector for All {{ fn names(self) -> Vec<String> {{ vec![{:?}.into()] }} }}",
            format!("{family}.*")
        )?;
        if events.iter().any(|(event, modes)| {
            event.starts_with(&format!("{family}.")) && modes.contains("intercept")
        }) {
            out.push_str(
                "impl super::InterceptSelector for All {}
",
            );
        }
        out.push_str(
            "}
",
        );
    }
    for count in 1..=12 {
        let types = (0..count).map(|i| format!("S{i}")).collect::<Vec<_>>();
        let tuple = format!("({},)", types.join(","));
        let bounds = types
            .iter()
            .map(|ty| format!("{ty}: Selector"))
            .collect::<Vec<_>>()
            .join(",");
        writeln!(out, "impl<{bounds}> sealed::Sealed for {tuple} {{}}
impl<{bounds}> Selector for {tuple} {{ fn names(self) -> Vec<String> {{ let mut names = Vec::new();")?;
        for i in 0..count {
            writeln!(
                out,
                "for name in self.{i}.names() {{ if !names.contains(&name) {{ names.push(name); }} }}"
            )?;
        }
        out.push_str(
            "names } }
",
        );
        let bounds = types
            .iter()
            .map(|ty| format!("{ty}: InterceptSelector"))
            .collect::<Vec<_>>()
            .join(",");
        writeln!(out, "impl<{bounds}> InterceptSelector for {tuple} {{}}")?;
    }
    out.push_str(
        "}
",
    );
    Ok(())
}
