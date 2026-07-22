package com.agora.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    private Upstream upstream = new Upstream();
    private String defaultAgentInstance = "default-email-agent";
    private List<AgentType> agentTypes = new ArrayList<>(List.of(AgentType.defaultEmailAgent()));
    private Jwt jwt = new Jwt();
    private LoginRateLimit loginRateLimit = new LoginRateLimit();
    private Credentials owner = new Credentials();
    private Credentials viewer = new Credentials();
    private Credentials admin = new Credentials();

    public Upstream getUpstream() { return upstream; }
    public void setUpstream(Upstream upstream) { this.upstream = upstream; }

    public String getDefaultAgentInstance() { return defaultAgentInstance; }
    public void setDefaultAgentInstance(String defaultAgentInstance) { this.defaultAgentInstance = defaultAgentInstance; }

    public List<AgentType> getAgentTypes() { return agentTypes; }
    public void setAgentTypes(List<AgentType> agentTypes) { this.agentTypes = agentTypes; }

    public Jwt getJwt() { return jwt; }
    public void setJwt(Jwt jwt) { this.jwt = jwt; }

    public LoginRateLimit getLoginRateLimit() { return loginRateLimit; }
    public void setLoginRateLimit(LoginRateLimit loginRateLimit) { this.loginRateLimit = loginRateLimit; }

    public Credentials getOwner() { return owner; }
    public void setOwner(Credentials owner) { this.owner = owner; }

    public Credentials getViewer() { return viewer; }
    public void setViewer(Credentials viewer) { this.viewer = viewer; }

    public Credentials getAdmin() { return admin; }
    public void setAdmin(Credentials admin) { this.admin = admin; }

    public static class Upstream {
        private String emailAgentUrl = "http://localhost:8000";

        public String getEmailAgentUrl() { return emailAgentUrl; }
        public void setEmailAgentUrl(String emailAgentUrl) { this.emailAgentUrl = emailAgentUrl; }
    }

    public static class AgentType {
        private String id = "";
        private String displayName = "";
        private String description = "";
        private List<String> capabilities = new ArrayList<>();
        private String basePath = "";
        private String healthPath = "/health";
        private String color = "";
        private String icon = "";

        public static AgentType defaultEmailAgent() {
            AgentType type = new AgentType();
            type.setId("email-agent");
            type.setDisplayName("Email Agent");
            type.setDescription("Autonomous email triage, drafting, validation, style learning, and mailbox operations.");
            type.setCapabilities(List.of("email_triage", "draft_approval", "gmail_sync", "style_learning", "cost_observability"));
            type.setBasePath("/api/agent");
            type.setHealthPath("/health");
            type.setColor("#38bdf8");
            type.setIcon("mail");
            return type;
        }

        public String getId() { return id; }
        public void setId(String id) { this.id = id; }

        public String getDisplayName() { return displayName; }
        public void setDisplayName(String displayName) { this.displayName = displayName; }

        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }

        public List<String> getCapabilities() { return capabilities; }
        public void setCapabilities(List<String> capabilities) { this.capabilities = capabilities; }

        public String getBasePath() { return basePath; }
        public void setBasePath(String basePath) { this.basePath = basePath; }

        public String getHealthPath() { return healthPath; }
        public void setHealthPath(String healthPath) { this.healthPath = healthPath; }

        public String getColor() { return color; }
        public void setColor(String color) { this.color = color; }

        public String getIcon() { return icon; }
        public void setIcon(String icon) { this.icon = icon; }
    }

    public static class LoginRateLimit {
        private boolean enabled = true;
        private long windowSeconds = 900;
        private long maxFailures = 5;
        private long globalMaxFailures = 100;

        public boolean isEnabled() { return enabled; }
        public void setEnabled(boolean enabled) { this.enabled = enabled; }

        public long getWindowSeconds() { return windowSeconds; }
        public void setWindowSeconds(long windowSeconds) { this.windowSeconds = windowSeconds; }

        public long getMaxFailures() { return maxFailures; }
        public void setMaxFailures(long maxFailures) { this.maxFailures = maxFailures; }

        public long getGlobalMaxFailures() { return globalMaxFailures; }
        public void setGlobalMaxFailures(long globalMaxFailures) { this.globalMaxFailures = globalMaxFailures; }
    }

    public static class Jwt {
        private String secret = "";
        private long ttlMinutes = 15;
        private long refreshTtlDays = 7;
        private boolean secureCookies = true;

        public String getSecret() { return secret; }
        public void setSecret(String secret) { this.secret = secret; }

        public long getTtlMinutes() { return ttlMinutes; }
        public void setTtlMinutes(long ttlMinutes) { this.ttlMinutes = ttlMinutes; }

        public long getRefreshTtlDays() { return refreshTtlDays; }
        public void setRefreshTtlDays(long refreshTtlDays) { this.refreshTtlDays = refreshTtlDays; }

        public boolean isSecureCookies() { return secureCookies; }
        public void setSecureCookies(boolean secureCookies) { this.secureCookies = secureCookies; }
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
