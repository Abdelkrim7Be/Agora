package com.agora.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    private Upstream upstream = new Upstream();
    private String defaultAgentInstance = "default-email-agent";
    // Mailbox the seeded instance expects OAuth to authorize. Blank on purpose:
    // the email-agent treats an empty identity as "no expectation" and skips
    // mailbox verification, which is the legacy single-user path. Any non-blank
    // value is compared against the address Google actually authorized and fails
    // closed on a mismatch — so a placeholder here makes the instance impossible
    // to connect. Set it to a real address to pin the seeded instance to one mailbox.
    private String defaultAgentMailbox = "";
    private List<AgentType> agentTypes = new ArrayList<>(List.of(AgentType.defaultEmailAgent()));
    private Jwt jwt = new Jwt();
    private Mfa mfa = new Mfa();
    private LoginRateLimit loginRateLimit = new LoginRateLimit();
    private Credentials owner = new Credentials();
    private Credentials viewer = new Credentials();
    private Credentials admin = new Credentials();
    /** Public base URL of the web app; invitation links are built from it. */
    private String appUrl = "http://localhost:5173";
    private long inviteExpiryHours = 48;
    private Smtp smtp = new Smtp();

    public String getAppUrl() { return appUrl; }
    public void setAppUrl(String appUrl) { this.appUrl = appUrl; }

    public long getInviteExpiryHours() { return inviteExpiryHours; }
    public void setInviteExpiryHours(long inviteExpiryHours) { this.inviteExpiryHours = inviteExpiryHours; }

    public Smtp getSmtp() { return smtp; }
    public void setSmtp(Smtp smtp) { this.smtp = smtp; }

    public Upstream getUpstream() { return upstream; }
    public void setUpstream(Upstream upstream) { this.upstream = upstream; }

    public String getDefaultAgentInstance() { return defaultAgentInstance; }
    public void setDefaultAgentInstance(String defaultAgentInstance) { this.defaultAgentInstance = defaultAgentInstance; }

    public String getDefaultAgentMailbox() { return defaultAgentMailbox; }
    public void setDefaultAgentMailbox(String defaultAgentMailbox) { this.defaultAgentMailbox = defaultAgentMailbox; }

    public List<AgentType> getAgentTypes() { return agentTypes; }
    public void setAgentTypes(List<AgentType> agentTypes) { this.agentTypes = agentTypes; }

    public Jwt getJwt() { return jwt; }
    public void setJwt(Jwt jwt) { this.jwt = jwt; }

    public Mfa getMfa() { return mfa; }
    public void setMfa(Mfa mfa) { this.mfa = mfa; }

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
        /**
         * How long to wait for the agent to answer a proxied request, in seconds.
         *
         * The slow calls are the ones that run a model: a tone rewrite or a
         * redraft against a local CPU-hosted model takes minutes, not seconds.
         * At the old fixed 150s the gateway aborted while the agent was still
         * working, and the browser was left on a spinner that never resolved —
         * so this has to follow the deployment's inference speed.
         */
        private int responseTimeoutSeconds = 150;
        /**
         * How long a per-instance summary may be reused, in seconds.
         *
         * Each summary costs three upstream calls and the listing builds one per
         * instance, so without this the landing page is O(instances x 3) round
         * trips every view. These are dashboard counters, not decisions. Set 0 to
         * disable — which is what the tests do, so a cached figure from one case
         * can never be served to the next.
         */
        private int summaryCacheSeconds = 15;

        public String getEmailAgentUrl() { return emailAgentUrl; }
        public void setEmailAgentUrl(String emailAgentUrl) { this.emailAgentUrl = emailAgentUrl; }

        public int getSummaryCacheSeconds() { return summaryCacheSeconds; }
        public void setSummaryCacheSeconds(int summaryCacheSeconds) {
            this.summaryCacheSeconds = summaryCacheSeconds;
        }

        public int getResponseTimeoutSeconds() { return responseTimeoutSeconds; }
        public void setResponseTimeoutSeconds(int responseTimeoutSeconds) {
            this.responseTimeoutSeconds = responseTimeoutSeconds;
        }
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
            type.setDescription("Trie les e-mails, rédige les réponses, apprend votre style et gère la boîte mail, chaque envoi restant sous validation.");
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

    public static class Mfa {
        private long challengeTtlMinutes = 5;
        private int recoveryCodeCount = 8;

        public long getChallengeTtlMinutes() { return challengeTtlMinutes; }
        public void setChallengeTtlMinutes(long challengeTtlMinutes) { this.challengeTtlMinutes = challengeTtlMinutes; }

        public int getRecoveryCodeCount() { return recoveryCodeCount; }
        public void setRecoveryCodeCount(int recoveryCodeCount) { this.recoveryCodeCount = recoveryCodeCount; }
    }

    public static class Smtp {
        private String host = "";
        private int port = 587;
        private String username = "";
        private String password = "";
        private String from = "";

        /** Without a host and a From there is nothing to send with; the invitation
         * endpoints fall back to returning the setup link for the admin to deliver. */
        public boolean isConfigured() {
            return host != null && !host.isBlank() && from != null && !from.isBlank();
        }

        public String getHost() { return host; }
        public void setHost(String host) { this.host = host; }

        public int getPort() { return port; }
        public void setPort(int port) { this.port = port; }

        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }

        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }

        public String getFrom() { return from; }
        public void setFrom(String from) { this.from = from; }
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
