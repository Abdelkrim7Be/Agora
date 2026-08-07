package com.agora.gateway.user;

import com.agora.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
public class UserSeeder implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(UserSeeder.class);

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final GatewayProperties props;

    public UserSeeder(UserRepository users, PasswordEncoder passwordEncoder, GatewayProperties props) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.props = props;
    }

    @Override
    public void run(String... args) {
        seed(props.getOwner(), "owner");
        seed(props.getViewer(), "viewer");
        seed(props.getAdmin(), "admin");
    }

    private void seed(GatewayProperties.Credentials credentials, String role) {
        String username = credentials.getUsername();
        String password = credentials.getPassword();
        String email = credentials.getEmail();
        if (username.isBlank() || password.isBlank()) return;

        var existing = users.findByUsername(username);
        if (existing.isPresent()) {
            // Backfill only. A seeded account created before the address was
            // configured could not be invited or reached from the team page; an
            // account that already has one keeps it, and the password is never
            // touched here.
            AppUser user = existing.get();
            if (!email.isBlank() && (user.getEmail() == null || user.getEmail().isBlank())) {
                user.setEmail(email);
                users.save(user);
                log.info("backfilled email for seeded user {}", username);
            }
            return;
        }

        AppUser user = new AppUser(username, passwordEncoder.encode(password), role);
        if (!email.isBlank()) {
            user.setEmail(email);
        }
        users.save(user);
        log.info("seeded user {} ({})", username, role);
    }
}
