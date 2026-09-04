package com.agora.gateway;

import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import com.agora.gateway.user.UserSeeder;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * A seeded account with no address cannot be invited, re-invited or reached from
 * the team page — which is the only thing an admin wants to do with the accounts
 * that exist before anyone signs up.
 */
@SpringBootTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:seederemaildb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=seed-owner",
        "gateway.owner.password=ownerpass",
        "gateway.owner.email=owner@example.com",
        "gateway.viewer.username=seed-viewer",
        "gateway.viewer.password=viewerpass",
        "gateway.admin.username=seed-admin",
        "gateway.admin.password=adminpass"
})
class UserSeederEmailTest {

    @Autowired
    private UserRepository users;

    @Autowired
    private UserSeeder seeder;

    @Autowired
    private PasswordEncoder passwordEncoder;

    @Autowired
    private GatewayProperties props;

    @Test
    void a_configured_email_is_seeded_onto_the_account() {
        assertThat(users.findByUsername("seed-owner")).isPresent()
                .get()
                .extracting(AppUser::getEmail)
                .isEqualTo("owner@example.com");
    }

    @Test
    void an_account_without_a_configured_email_is_seeded_without_one() {
        assertThat(users.findByUsername("seed-viewer")).isPresent()
                .get()
                .extracting(AppUser::getEmail)
                .isNull();
    }

    @Test
    void an_address_is_backfilled_onto_an_account_seeded_before_it_was_configured() {
        users.save(new AppUser("legacy-admin", passwordEncoder.encode("x"), "admin"));
        props.getAdmin().setUsername("legacy-admin");
        props.getAdmin().setPassword("x");
        props.getAdmin().setEmail("admin@example.com");

        seeder.run();

        assertThat(users.findByUsername("legacy-admin")).isPresent()
                .get()
                .extracting(AppUser::getEmail)
                .isEqualTo("admin@example.com");
    }

    @Test
    void an_address_already_set_is_never_overwritten() {
        AppUser user = new AppUser("kept-address", passwordEncoder.encode("x"), "owner");
        user.setEmail("chosen-by-a-human@example.com");
        users.save(user);
        props.getOwner().setUsername("kept-address");
        props.getOwner().setPassword("x");
        props.getOwner().setEmail("from-config@example.com");

        seeder.run();

        assertThat(users.findByUsername("kept-address")).isPresent()
                .get()
                .extracting(AppUser::getEmail)
                .isEqualTo("chosen-by-a-human@example.com");
    }

    @Test
    void reseeding_never_changes_an_existing_password() {
        AppUser user = new AppUser("stable-password", passwordEncoder.encode("original"), "owner");
        users.save(user);
        String hashBefore = user.getPasswordHash();
        props.getOwner().setUsername("stable-password");
        props.getOwner().setPassword("a-different-password");
        props.getOwner().setEmail("someone@example.com");

        seeder.run();

        assertThat(users.findByUsername("stable-password")).isPresent()
                .get()
                .extracting(AppUser::getPasswordHash)
                .isEqualTo(hashBefore);
    }
}
