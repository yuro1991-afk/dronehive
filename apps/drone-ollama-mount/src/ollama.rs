//! Ollama API client — parent AI controller for drones.

use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Debug, Clone)]
pub struct OllamaClient {
    pub host: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TagsResponse {
    pub models: Vec<ModelTag>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModelTag {
    pub name: String,
}

#[derive(Debug, Clone)]
pub struct OllamaStatus {
    pub ok: bool,
    pub detail: String,
    pub models: Vec<String>,
    pub version: Option<String>,
}

#[derive(Debug, Serialize)]
struct GenerateReq<'a> {
    model: &'a str,
    prompt: &'a str,
    stream: bool,
    options: GenerateOpts,
}

#[derive(Debug, Serialize)]
struct GenerateOpts {
    num_predict: u32,
}

#[derive(Debug, Deserialize)]
struct GenerateResp {
    response: Option<String>,
    error: Option<String>,
}

impl OllamaClient {
    pub fn new(host: impl Into<String>) -> Self {
        Self { host: host.into() }
    }

    pub fn status(&self) -> OllamaStatus {
        let version = self
            .get_json(&format!("{}/api/version", self.host))
            .ok()
            .and_then(|v: serde_json::Value| {
                v.get("version")
                    .and_then(|x| x.as_str())
                    .map(|s| s.to_string())
            });

        match self.get_json::<TagsResponse>(&format!("{}/api/tags", self.host)) {
            Ok(tags) => {
                let models: Vec<String> = tags.models.into_iter().map(|m| m.name).collect();
                OllamaStatus {
                    ok: true,
                    detail: format!("{} models", models.len()),
                    models,
                    version,
                }
            }
            Err(e) => OllamaStatus {
                ok: false,
                detail: e,
                models: vec![],
                version,
            },
        }
    }

    /// Parent Ollama model issues a short controller plan for the drone goal.
    pub fn controller_plan(&self, model: &str, goal: &str) -> Result<String, String> {
        let prompt = format!(
            "You are an AI commander controlling worker drones (not a human chat UI).\n\
             Goal: {goal}\n\
             Reply with ONE short task brief for the drone fabric (max 40 words). No fluff."
        );
        let body = GenerateReq {
            model,
            prompt: &prompt,
            stream: false,
            options: GenerateOpts { num_predict: 80 },
        };
        let url = format!("{}/api/generate", self.host);
        let resp: GenerateResp = self
            .post_json(&url, &body)
            .map_err(|e| format!("ollama generate: {e}"))?;
        if let Some(err) = resp.error {
            return Err(err);
        }
        Ok(resp.response.unwrap_or_default().trim().to_string())
    }

    fn get_json<T: for<'de> Deserialize<'de>>(&self, url: &str) -> Result<T, String> {
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(std::time::Duration::from_secs(2))
            .timeout_read(std::time::Duration::from_secs(10))
            .build();
        agent
            .get(url)
            .call()
            .map_err(|e| e.to_string())?
            .into_json()
            .map_err(|e| e.to_string())
    }

    fn post_json<B: Serialize, T: for<'de> Deserialize<'de>>(
        &self,
        url: &str,
        body: &B,
    ) -> Result<T, String> {
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(std::time::Duration::from_secs(3))
            .timeout_read(std::time::Duration::from_secs(120))
            .build();
        agent
            .post(url)
            .send_json(body)
            .map_err(|e| e.to_string())?
            .into_json()
            .map_err(|e| e.to_string())
    }

    pub fn ping_json(&self) -> serde_json::Value {
        let st = self.status();
        json!({
            "ok": st.ok,
            "detail": st.detail,
            "models": st.models,
            "version": st.version,
            "host": self.host,
        })
    }
}
