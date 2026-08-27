use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationManifest {
    pub status: String,
    pub schema_revision: String,
    pub protocol_version: String,
    pub schema_manifest: PinnedFile,
    pub profile: PinnedFile,
    pub compatibility_cases: PinnedFile,
}

#[derive(Debug, Deserialize)]
pub struct PinnedFile {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SchemaManifest {
    pub draft_version: String,
    pub dialect: String,
    pub documents: Vec<SchemaDocument>,
}

#[derive(Debug, Deserialize)]
pub struct SchemaDocument {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub schema_revision: String,
    pub protocol_version: String,
    pub public_roots: Vec<PublicRoot>,
    pub stable_names: std::collections::BTreeMap<String, String>,
    pub discriminators: Vec<Discriminator>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicRoot {
    pub name: String,
    pub schema: String,
    #[serde(default)]
    pub method: Option<String>,
    #[serde(default)]
    pub protocol_version_pointer: Option<String>,
    #[serde(default)]
    pub response_context_required: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Discriminator {
    pub schema: String,
    pub pointer: String,
    pub property: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Ir {
    pub schema_revision: String,
    pub protocol_version: String,
    pub roots: Vec<PublicRoot>,
    pub types: Vec<NamedType>,
}

#[derive(Debug, Serialize)]
pub struct NamedType {
    pub name: String,
    pub source: String,
    pub shape: Shape,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum Shape {
    Any,
    Never,
    Null,
    Boolean,
    Integer,
    Number,
    String,
    Literal {
        value: Value,
    },
    Enum {
        values: Vec<Value>,
        open_strings: bool,
    },
    Array {
        items: Box<Shape>,
    },
    Object {
        properties: Vec<Property>,
        forbidden_property_sets: Vec<Vec<String>>,
        additional: AdditionalProperties,
    },
    Union {
        mode: UnionMode,
        variants: Vec<Shape>,
        #[serde(skip_serializing_if = "Option::is_none")]
        discriminator: Option<String>,
    },
    Intersection {
        variants: Vec<Shape>,
    },
    Ref {
        name: String,
    },
}

#[derive(Debug, Clone, Serialize)]
pub struct Property {
    pub wire_name: String,
    pub required: bool,
    pub shape: Shape,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", content = "value", rename_all = "camelCase")]
pub enum AdditionalProperties {
    Allowed,
    Forbidden,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum UnionMode {
    AnyOf,
    OneOf,
}
