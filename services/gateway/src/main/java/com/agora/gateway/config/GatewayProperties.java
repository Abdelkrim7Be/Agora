package com.agora.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    private Upstream upstream = new Upstream();
    private Jwt jwt = new Jwt();
    private Credentials owner = new Credentials();
    private Credentials viewer = new Credentials();

    public Upstream getUpstream() { return upstream; }
    public void setUpstream(Upstream upstream) { this.upstream = upstream; }

    public Jwt getJwt() { return jwt; }
    public void setJwt(Jwt jwt) { this.jwt = jwt; }

    public Credentials getOwner() { return owner; }
    public void setOwner(Credentials owner) { this.owner = owner; }

    public Credentials getViewer() { return viewer; }
    public void setViewer(Credentials viewer) { this.viewer = viewer; }

    public static class Upstream {
        private String emailAgentUrl = "http://localhost:8000";

        public String getEmailAgentUrl() { return emailAgentUrl; }
        public void setEmailAgentUrl(String emailAgentUrl) { this.emailAgentUrl = emailAgentUrl; }
    }

    public static class Jwt {
        private String secret = "";
        private long ttlMinutes = 60;

        public String getSecret() { return secret; }
        public void setSecret(String secret) { this.secret = secret; }

        public long getTtlMinutes() { return ttlMinutes; }
        public void setTtlMinutes(long ttlMinutes) { this.ttlMinutes = ttlMinutes; }
    }

    public static class Credentials {
        private String username = "";
        private String password = "";

        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }

        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }
    }
}
