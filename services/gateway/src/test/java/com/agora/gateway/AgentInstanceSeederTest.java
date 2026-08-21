package com.agora.gateway;

import com.agora.gateway.agent.AgentInstance;
import com.agora.gateway.agent.AgentInstanceRepository;
import com.agora.gateway.agent.AgentInstanceSeeder;
import com.agora.gateway.config.GatewayProperties;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The seeded instance's mailbox_identity is not cosmetic: the email-agent compares
 * it against the mailbox Google authorized during the OAuth callback and fails
 * closed on a mismatch. Seeding a non-address placeholder therefore shipped an
 * instance that could never connect Gmail on any clean deployment.
 */
class AgentInstanceSeederTest {

    private AgentInstance seedWith(GatewayProperties props) throws Exception {
        AgentInstanceRepository repo = mock(AgentInstanceRepository.class);
        when(repo.count()).thenReturn(0L);

        new AgentInstanceSeeder(repo, props).run();

        ArgumentCaptor<AgentInstance> saved = ArgumentCaptor.forClass(AgentInstance.class);
        verify(repo).save(saved.capture());
        return saved.getValue();
    }

    @Test
    void seedsABlankMailboxIdentityByDefault() throws Exception {
        AgentInstance instance = seedWith(new GatewayProperties());

        // Blank is the documented "no expectation" path: verification is skipped and
        // whichever mailbox the user authorizes is accepted.
        assertThat(instance.getMailboxIdentity()).isEmpty();
    }

    @Test
    void neverSeedsAPlaceholderThatCannotAuthorize() throws Exception {
        AgentInstance instance = seedWith(new GatewayProperties());

        // Regression guard for the literal that shipped before: any non-address value
        // here is compared against the real authorized mailbox and can only fail.
        assertThat(instance.getMailboxIdentity()).doesNotContain("default-mailbox");
    }

    @Test
    void pinsTheMailboxWhenOneIsConfigured() throws Exception {
        GatewayProperties props = new GatewayProperties();
        props.setDefaultAgentMailbox("  ops@example.com  ");

        AgentInstance instance = seedWith(props);

        // Trimmed: a stray space would make the equality check against Google's
        // reported address fail for a value that is otherwise correct.
        assertThat(instance.getMailboxIdentity()).isEqualTo("ops@example.com");
    }

    @Test
    void doesNotSeedWhenInstancesAlreadyExist() {
        AgentInstanceRepository repo = mock(AgentInstanceRepository.class);
        when(repo.count()).thenReturn(1L);

        new AgentInstanceSeeder(repo, new GatewayProperties()).run();

        // A deliberately deleted default instance must stay deleted across restarts.
        verify(repo, never()).save(any());
    }

    @Test
    void seedsTheInstanceUnderTheConfiguredOwnerAccount() throws Exception {
        // The poller stamps every run with the owning instance's creator, and
        // tenant-scoped reads filter on the requesting user. A creator nobody can
        // log in as means the validation queue is always empty.
        GatewayProperties props = new GatewayProperties();
        props.getOwner().setUsername("patron");

        assertThat(seedWith(props).getCreatedBy()).isEqualTo("patron");
    }

    @Test
    void fallsBackToTheAdminAccountWhenNoOwnerIsConfigured() throws Exception {
        GatewayProperties props = new GatewayProperties();
        props.getOwner().setUsername("");
        props.getAdmin().setUsername("root");

        assertThat(seedWith(props).getCreatedBy()).isEqualTo("root");
    }

    @Test
    void aConfiguredDeploymentNeverSeedsTheUnloggableSystemCreator() throws Exception {
        // Regression guard: "system" was the shipped literal, and it made the
        // seeded instance's whole run history invisible to every real account.
        // With no accounts configured at all there is nobody to own it and the
        // fallback stands — but that deployment has no login either.
        GatewayProperties props = new GatewayProperties();
        props.getOwner().setUsername("owner");
        props.getAdmin().setUsername("admin");

        assertThat(seedWith(props).getCreatedBy()).isNotEqualTo("system");
    }
}
