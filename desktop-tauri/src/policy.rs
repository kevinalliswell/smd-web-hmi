//! Shell configuration only: never load service.env, pairing material or a database.
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use url::{Host, Url};

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum FailureKind {
    ConfigurationMissing,
    ConfigurationInvalid,
    ServiceUnreachable,
    CertificateError,
    ServiceResponseInvalid,
    RuntimeMissing,
    PageLoadFailed,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Diagnostic {
    pub kind: FailureKind,
    pub message: String,
}

impl Diagnostic {
    pub fn new(kind: FailureKind, message: &str) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }
}

#[derive(Deserialize)]
struct ClientConfig {
    url: String,
}

/// Configuration stores an origin, not arbitrary navigation arguments or credentials.
pub fn parse_origin(input: &str) -> Result<Url, Diagnostic> {
    let invalid = || {
        Diagnostic::new(
            FailureKind::ConfigurationInvalid,
            "client.json 的 url 必须是 http/https 本机地址，且不含凭据、路径、查询或片段。",
        )
    };
    let parsed = Url::parse(input).map_err(|_| invalid())?;
    let loopback = match parsed.host() {
        Some(Host::Domain(name)) => name == "localhost",
        Some(Host::Ipv4(ip)) => ip == std::net::Ipv4Addr::LOCALHOST,
        Some(Host::Ipv6(ip)) => ip == std::net::Ipv6Addr::LOCALHOST,
        None => false,
    };
    if !loopback
        || !matches!(parsed.scheme(), "http" | "https")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.path() != "/"
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || parsed.port() == Some(0)
        || input.chars().any(|c| c.is_control() || c == '\\')
    {
        return Err(invalid());
    }
    Ok(parsed)
}

pub fn read_origin(data: &Path) -> Result<Url, Diagnostic> {
    let path = data.join("client.json");
    let bytes = fs::read(path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            Diagnostic::new(
                FailureKind::ConfigurationMissing,
                "未找到 client.json，请由维护人员核对安装配置。",
            )
        } else {
            Diagnostic::new(
                FailureKind::ConfigurationInvalid,
                "无法读取 client.json，请核对文件权限。",
            )
        }
    })?;
    // Existing Windows provisioning may produce UTF-8 BOM.
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&bytes);
    let config: ClientConfig = serde_json::from_slice(bytes).map_err(|_| {
        Diagnostic::new(
            FailureKind::ConfigurationInvalid,
            "client.json 必须是含 url 的 UTF-8 JSON 文件。",
        )
    })?;
    parse_origin(&config.url)
}

pub fn allows_navigation(origin: &Url, target: &Url) -> bool {
    target.origin() == origin.origin()
        && target.username().is_empty()
        && target.password().is_none()
        && matches!(target.scheme(), "http" | "https")
}

pub fn require_runtime(version: &Path) -> Result<std::path::PathBuf, Diagnostic> {
    let runtime = version.join("webview2");
    if !(runtime.join("msedgewebview2.exe").is_file() && runtime.join("icudtl.dat").is_file()) {
        return Err(Diagnostic::new(
            FailureKind::RuntimeMissing,
            "缺少随版本交付的 Fixed WebView2。请由维护人员修复安装后重试；不会改用系统运行时。",
        ));
    }
    Ok(runtime)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_external_credentials_and_configuration_paths() {
        for input in [
            "https://example.com",
            "http://127.0.0.1.evil.example",
            "file:///C:/x",
            "http://0.0.0.0:8000",
            "http://127.0.0.2",
            "http://[::ffff:127.0.0.1]",
            "http://user@127.0.0.1",
            "http://:secret@localhost",
            "http://localhost/login",
            "http://localhost/?token=secret",
            "http://localhost/#x",
            "http://localhost:0",
            "http://localhost\\@evil.example",
            "http://local\nhost",
        ] {
            assert!(parse_origin(input).is_err(), "accepted {input}");
        }
    }

    #[test]
    fn retains_explicit_protocol_and_port_without_fallback() {
        for input in [
            "http://127.0.0.1:8000",
            "https://localhost:8443/",
            "https://[::1]:9443",
        ] {
            let origin = parse_origin(input).unwrap();
            assert!(allows_navigation(
                &origin,
                &origin.join("/test?from=overview").unwrap()
            ));
            for other in [
                "https://example.com/",
                "http://127.0.0.1:8001/",
                "file:///C:/x",
                "data:text/html,x",
                "https://localhost:8444/",
            ] {
                assert!(!allows_navigation(&origin, &Url::parse(other).unwrap()));
            }
        }
    }

    #[test]
    fn loopback_alias_and_https_downgrade_are_different_origins() {
        let origin = parse_origin("https://localhost:8000").unwrap();
        assert!(!allows_navigation(
            &origin,
            &Url::parse("https://127.0.0.1:8000/").unwrap()
        ));
        assert!(!allows_navigation(
            &origin,
            &Url::parse("http://localhost:8000/").unwrap()
        ));
        assert!(!allows_navigation(
            &origin,
            &Url::parse("https://user@localhost:8000/").unwrap()
        ));
    }
}
