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
        "subscription" => ", intercept, observe, InterceptBuilder, ObserveBuilder, Unset".into(),
        "content" => ", ContentSelection as Selection".into(),
        "event" => ", EventType, EventType::*".into(),
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
        self.subscriptions.push(subscription.into().into());
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
    out.push_str(r#"
impl ContentSelection {
    /// Select descriptors without content bodies for every category.
    pub fn metadata() -> Self { Self::new(ContentSelectionDefault::Metadata) }
}
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
    event: EventType,
    timeout: T,
    failure: F,
    content: C,
}
/// Start an interception subscription; no delivery policy is defaulted.
pub fn intercept(event: EventType) -> InterceptBuilder {
    InterceptBuilder { event, timeout: Unset, failure: Unset, content: Unset }
}
impl<T, F, C> InterceptBuilder<T, F, C> {
    /// Set an exact whole-millisecond timeout.
    /// # Panics
    /// Panics if the duration contains fractional milliseconds; never rounds or truncates.
    pub fn timeout(self, duration: std::time::Duration) -> InterceptBuilder<Integer, F, C> {
        assert_eq!(duration.subsec_nanos() % 1_000_000, 0, "subscription timeout must be whole milliseconds");
        let timeout = serde_json::from_str(&duration.as_millis().to_string()).expect("milliseconds are an integer");
        InterceptBuilder { event: self.event, timeout, failure: self.failure, content: self.content }
    }
    pub fn fail_open(self) -> InterceptBuilder<T, InterceptSubscriptionFailurePolicy, C> {
        InterceptBuilder { event: self.event, timeout: self.timeout, failure: InterceptSubscriptionFailurePolicy::FailOpen, content: self.content }
    }
    pub fn fail_closed(self) -> InterceptBuilder<T, InterceptSubscriptionFailurePolicy, C> {
        InterceptBuilder { event: self.event, timeout: self.timeout, failure: InterceptSubscriptionFailurePolicy::FailClosed, content: self.content }
    }
    pub fn content(self, content: ContentSelection) -> InterceptBuilder<T, F, ContentSelection> {
        InterceptBuilder { event: self.event, timeout: self.timeout, failure: self.failure, content }
    }
}
impl From<InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>> for InterceptSubscription {
    fn from(value: InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>) -> Self {
        Self::new(value.content, vec![InterceptSubscriptionEventsItem::Custom(value.event.as_str().to_owned())], value.failure, value.timeout)
    }
}
impl From<InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>> for BackendSubscriptionsItem {
    fn from(value: InterceptBuilder<Integer, InterceptSubscriptionFailurePolicy, ContentSelection>) -> Self {
        InterceptSubscription::from(value).into()
    }
}
#[derive(Debug, Clone)]
pub struct ObserveBuilder<C = Unset> { event: EventType, content: C }
/// Start an observation subscription; content selection must be explicit.
pub fn observe(event: EventType) -> ObserveBuilder { ObserveBuilder { event, content: Unset } }
impl<C> ObserveBuilder<C> {
    pub fn content(self, content: ContentSelection) -> ObserveBuilder<ContentSelection> {
        ObserveBuilder { event: self.event, content }
    }
}
impl From<ObserveBuilder<ContentSelection>> for ObserveSubscription {
    fn from(value: ObserveBuilder<ContentSelection>) -> Self {
        Self::new(value.content, vec![ObserveSubscriptionEventsItem::Custom(value.event.as_str().to_owned())])
    }
}
impl From<ObserveBuilder<ContentSelection>> for BackendSubscriptionsItem {
    fn from(value: ObserveBuilder<ContentSelection>) -> Self { ObserveSubscription::from(value).into() }
}

"#);
    Ok(())
}
