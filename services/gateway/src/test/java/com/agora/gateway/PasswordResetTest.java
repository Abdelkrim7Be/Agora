package com.agora.gateway;

import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.InvitationService;
import com.agora.gateway.user.UserInvitation;
import com.agora.gateway.user.UserInvitationRepository;
import com.agora.gateway.user.UserRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The security properties of self-service password reset.
 *
 * These are not incidental behaviours to be adjusted for convenience: each one
 * is the difference between a reset flow and an account-takeover endpoint.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:pwresetdb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=pwreset-owner",
        "gateway.owner.password=ownerpass",
        "gateway.admin.username=pwreset-admin",
        "gateway.admin.password=adminpass",
        "gateway.app-url=https://app.example.test"
})
class PasswordResetTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;
    @Autowired private UserRepository users;
    @Autowired private UserInvitationRepository invitations;
    @Autowired private InvitationService invitationService;
    @Autowired private PasswordEncoder passwordEncoder;

    private AppUser account(String username, String email, boolean enabled) {
        AppUser user = new AppUser(username, passwordEncoder.encode("original-password"), "viewer");
        user.setEmail(email);
        user.setEnabled(enabled);
        return users.save(user);
    }

    private String forgot(String email) throws Exception {
        return mockMvc.perform(post("/auth/forgot-password")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("email", email))))
                .andExpect(status().isAccepted())
                .andReturn().getResponse().getContentAsString();
    }

    // --- (1) the link never leaves in the response ---------------------------

    @Test
    void the_response_never_carries_the_reset_link() throws Exception {
        // Returning it would let anyone request a reset for any address and read
        // the takeover link out of their own HTTP response.
        account("linkleak", "linkleak@example.test", true);

        String body = forgot("linkleak@example.test");

        assertThat(body).doesNotContain("/invite/");
        assertThat(body).doesNotContain("app.example.test");
        assertThat(body.toLowerCase()).doesNotContain("token");
    }

    @Test
    void a_reset_token_is_still_issued_even_though_it_is_not_returned() throws Exception {
        // The link is real and waiting to be mailed; it simply never comes back
        // over HTTP.
        AppUser user = account("tokenissued", "tokenissued@example.test", true);

        forgot("tokenissued@example.test");

        List<UserInvitation> open = invitations.findByUserIdAndConsumedAtIsNull(user.getId());
        assertThat(open).hasSize(1);
        assertThat(open.get(0).isReset()).isTrue();
    }

    // --- (2) the answer never depends on the account -------------------------

    @Test
    void an_unknown_address_answers_exactly_like_a_known_one() throws Exception {
        // Any difference here is a way to test which addresses hold accounts.
        account("known", "known@example.test", true);

        assertThat(forgot("nobody-here@example.test")).isEqualTo(forgot("known@example.test"));
    }

    @Test
    void a_disabled_account_answers_exactly_like_an_active_one() throws Exception {
        account("activeone", "activeone@example.test", true);
        account("disabledone", "disabledone@example.test", false);

        assertThat(forgot("disabledone@example.test")).isEqualTo(forgot("activeone@example.test"));
    }

    // --- (3) closed accounts get no link at all ------------------------------

    @Test
    void a_disabled_account_is_issued_no_token() throws Exception {
        // A reset must not be a way back into an account that was closed.
        AppUser user = account("noreset", "noreset@example.test", false);

        forgot("noreset@example.test");

        assertThat(invitations.findByUserIdAndConsumedAtIsNull(user.getId())).isEmpty();
    }

    // --- redeeming a reset must not enable a disabled account ----------------

    @Test
    void redeeming_a_reset_does_not_enable_an_account_disabled_meanwhile() {
        // The window: link sent while active, account disabled, link then used.
        // Enabling here would let the locked-out person undo being locked out.
        AppUser user = account("disabledlater", "disabledlater@example.test", true);
        InvitationService.Issued issued = invitationService.createForReset(user);

        user.setEnabled(false);
        users.save(user);

        assertThat(invitationService.consume(issued.token(), "a-brand-new-password")).isTrue();
        assertThat(users.findById(user.getId()).orElseThrow().isEnabled()).isFalse();
    }

    @Test
    void redeeming_an_invitation_still_enables_the_account() {
        // The counterpart: an invited account is disabled until its password is
        // set, so the invitation path must keep enabling it.
        AppUser user = account("invited", "invited@example.test", false);
        InvitationService.Issued issued = invitationService.create(user, "admin");

        assertThat(invitationService.consume(issued.token(), "a-brand-new-password")).isTrue();
        assertThat(users.findById(user.getId()).orElseThrow().isEnabled()).isTrue();
    }

    @Test
    void a_reset_actually_changes_the_password() {
        AppUser user = account("changesit", "changesit@example.test", true);
        InvitationService.Issued issued = invitationService.createForReset(user);

        invitationService.consume(issued.token(), "a-brand-new-password");

        AppUser reloaded = users.findById(user.getId()).orElseThrow();
        assertThat(passwordEncoder.matches("a-brand-new-password", reloaded.getPasswordHash())).isTrue();
        assertThat(passwordEncoder.matches("original-password", reloaded.getPasswordHash())).isFalse();
    }

    @Test
    void a_reset_token_is_single_use() {
        AppUser user = account("singleuse", "singleuse@example.test", true);
        InvitationService.Issued issued = invitationService.createForReset(user);

        assertThat(invitationService.consume(issued.token(), "first-new-password")).isTrue();
        assertThat(invitationService.consume(issued.token(), "second-new-password")).isFalse();
    }

    @Test
    void requesting_again_retires_the_previous_link() throws Exception {
        // Two live tokens for one account means the first link — which may have
        // gone to the wrong inbox — still works.
        AppUser user = account("resend", "resend@example.test", true);
        InvitationService.Issued first = invitationService.createForReset(user);

        forgot("resend@example.test");

        assertThat(invitationService.consume(first.token(), "should-not-work")).isFalse();
    }

    @Test
    void the_endpoint_needs_no_authentication() throws Exception {
        // Somebody who forgot their password has no token to present.
        mockMvc.perform(post("/auth/forgot-password")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("email", "anyone@example.test"))))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("accepted"));
    }
}
