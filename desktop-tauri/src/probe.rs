use crate::policy::{Diagnostic, FailureKind};
use serde::Deserialize;
use std::{error::Error, time::Duration};
use url::Url;

#[derive(Deserialize)]
struct Health {
    data: HealthData,
}
#[derive(Deserialize)]
struct HealthData {
    status: String,
    version: String,
}

pub fn request_failure(error: &reqwest::Error) -> Diagnostic {
    let mut cause: Option<&(dyn Error + 'static)> = Some(error);
    while let Some(current) = cause {
        if current.downcast_ref::<native_tls::Error>().is_some() {
            return Diagnostic::new(
                FailureKind::CertificateError,
                "TLS 证书或握手校验失败。请核对证书信任、有效期和本机地址；不会忽略证书错误。",
            );
        }
        cause = current.source();
    }
    Diagnostic::new(
        FailureKind::ServiceUnreachable,
        "无法连接本机后台，或连接已超时。请核对服务状态和端口，然后重试。",
    )
}

pub async fn check_service(origin: &Url) -> Result<(), Diagnostic> {
    // Do not follow redirects to another host or use ambient proxy settings.
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|_| {
            Diagnostic::new(FailureKind::ServiceUnreachable, "无法初始化本机连接检查。")
        })?;
    let url = origin
        .join("/api/system/health")
        .expect("validated HTTP origin");
    let response = client
        .get(url)
        .send()
        .await
        .map_err(|e| request_failure(&e))?;
    let invalid = || {
        Diagnostic::new(
            FailureKind::ServiceResponseInvalid,
            "本机地址未返回预期的 SMD 健康响应。请核对端口和服务版本。",
        )
    };
    if response.status() != reqwest::StatusCode::OK
        || response.content_length().is_some_and(|n| n > 65536)
    {
        return Err(invalid());
    }
    let mut response = response;
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|e| request_failure(&e))? {
        if body.len() + chunk.len() > 65536 {
            return Err(invalid());
        }
        body.extend_from_slice(&chunk);
    }
    validate_health(&body).then_some(()).ok_or_else(invalid)
}

fn validate_health(body: &[u8]) -> bool {
    serde_json::from_slice::<Health>(body).is_ok_and(|h| {
        matches!(h.data.status.as_str(), "ready" | "not_ready") && !h.data.version.is_empty()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn offline_device_does_not_hide_login_or_history() {
        assert!(validate_health(
            br#"{"data":{"status":"not_ready","version":"0.3.0","checks":{"hostcomm":false}}}"#
        ));
        assert!(validate_health(
            br#"{"data":{"status":"ready","version":"0.3.0"}}"#
        ));
    }
    #[test]
    fn unrelated_http_listener_is_not_a_healthy_service() {
        for body in [
            b"<html>proxy login</html>".as_slice(),
            br#"{"data":{"status":"ok","version":"1"}}"#,
            br#"{"data":{"status":"ready","version":""}}"#,
        ] {
            assert!(!validate_health(body));
        }
    }
}
