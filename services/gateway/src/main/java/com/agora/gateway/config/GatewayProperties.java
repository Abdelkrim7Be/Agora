package com.agora.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    private Upstream upstream = new Upstream();

    public Upstream getUpstream() {
        return upstream;
    }

    public void setUpstream(Upstream upstream) {
        this.upstream = upstream;
    }

    public static class Upstream {
        private String emailAgentUrl = "http://localhost:8000";

        public String getEmailAgentUrl() {
            return emailAgentUrl;
        }

        public void setEmailAgentUrl(String emailAgentUrl) {
            this.emailAgentUrl = emailAgentUrl;
        }
    }
}
