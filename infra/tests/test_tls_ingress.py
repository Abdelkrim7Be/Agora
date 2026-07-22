from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
DYNAMIC = yaml.safe_load((ROOT / "traefik" / "dynamic.yml").read_text())
PRODUCTION = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text())


class TlsIngressTest(unittest.TestCase):
    def test_only_ingress_publishes_a_host_port(self):
        published = {
            name: service["ports"]
            for name, service in COMPOSE["services"].items()
            if service.get("ports")
        }

        self.assertEqual(published, {"ingress": ["443:8443"]})

    def test_ingress_is_hardened_and_has_no_docker_socket(self):
        ingress = COMPOSE["services"]["ingress"]
        volumes = ingress["volumes"]

        self.assertEqual(ingress["profiles"], ["production"])
        self.assertEqual(ingress["image"], "traefik:v3.7.1@sha256:6b9cbca6fac42ab0075f5437d8dc1685cfd188626d8d515839ea94f8b6271c42")
        self.assertNotEqual(ingress["user"], "0")
        self.assertTrue(ingress["read_only"])
        self.assertEqual(ingress["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", ingress["security_opt"])
        self.assertFalse(any("docker.sock" in volume for volume in volumes))
        self.assertTrue(all(volume.endswith(":ro") for volume in volumes))

    def test_tls_router_targets_only_internal_web_service(self):
        router = DYNAMIC["http"]["routers"]["agora"]
        servers = DYNAMIC["http"]["services"]["web"]["loadBalancer"]["servers"]
        tls_options = DYNAMIC["tls"]["options"]["default"]

        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["service"], "web")
        self.assertEqual(servers, [{"url": "http://web:8080"}])
        self.assertEqual(tls_options["minVersion"], "VersionTLS12")
        self.assertTrue(tls_options["sniStrict"])

    def test_private_certificates_are_not_committed(self):
        cert_dir = ROOT / "traefik" / "certs"

        self.assertEqual({path.name for path in cert_dir.iterdir()}, {".gitignore"})

    def test_certificate_paths_are_fixed_inside_ingress(self):
        certificate = DYNAMIC["tls"]["certificates"][0]

        self.assertEqual(certificate["certFile"], "/certs/tls.crt")
        self.assertEqual(certificate["keyFile"], "/certs/tls.key")

    def test_https_scheme_survives_web_proxy(self):
        for path in (
            ROOT.parent / "services" / "web" / "nginx.conf",
            ROOT.parent / "services" / "web-react" / "nginx.conf",
        ):
            config = path.read_text()
            self.assertIn("map $http_x_forwarded_proto $agora_forwarded_proto", config)
            self.assertIn(
                "proxy_set_header X-Forwarded-Proto $agora_forwarded_proto;",
                config,
            )

    def test_production_defaults_do_not_generate_http_callbacks(self):
        compose_text = (ROOT / "docker-compose.yml").read_text()

        self.assertIn(
            "GMAIL_OAUTH_REDIRECT_URI:-https://localhost/api/agent/connect/gmail/callback",
            compose_text,
        )
        self.assertIn(
            "GATEWAY_CORS_ALLOWED_ORIGINS:-https://localhost",
            compose_text,
        )


    def test_gateway_uses_short_lived_access_tokens_and_secure_refresh_cookie(self):
        gateway = COMPOSE["services"]["gateway"]["environment"]

        self.assertEqual(gateway["GATEWAY_JWT_TTL_MINUTES"], "${GATEWAY_JWT_TTL_MINUTES:-15}")
        self.assertEqual(gateway["GATEWAY_JWT_REFRESH_TTL_DAYS"], "${GATEWAY_JWT_REFRESH_TTL_DAYS:-7}")
        self.assertEqual(gateway["GATEWAY_JWT_SECURE_COOKIES"], "${GATEWAY_JWT_SECURE_COOKIES:-true}")

    def test_production_overlay_requires_vault_and_encryption(self):
        agent = PRODUCTION["services"]["email-agent"]
        environment = agent["environment"]

        self.assertEqual(environment["AGENT_TOKEN_STORE_BACKEND"], "vault")
        self.assertEqual(environment["AGENT_TOKEN_ENCRYPTION_REQUIRED"], "true")
        self.assertEqual(environment["AGENT_TOKEN_ENCRYPTION_KEY_FILE"], "/run/secrets/agora_token_keys")
        self.assertEqual(environment["AGENT_SECRET_MANAGER_TOKEN_FILE"], "/run/secrets/agora_vault_token")
        self.assertEqual(set(agent["secrets"]), {"agora_token_keys", "agora_vault_token"})
        self.assertTrue(any(item.startswith("/tmp:") for item in agent["tmpfs"]))

    def test_production_secret_files_are_not_committed(self):
        secret_dir = ROOT / "secrets"

        self.assertEqual({path.name for path in secret_dir.iterdir()}, {".gitignore"})


if __name__ == "__main__":
    unittest.main()
