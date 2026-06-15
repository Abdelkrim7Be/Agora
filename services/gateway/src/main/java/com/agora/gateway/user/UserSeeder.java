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
        seed(props.getOwner().getUsername(), props.getOwner().getPassword(), "owner");
        seed(props.getViewer().getUsername(), props.getViewer().getPassword(), "viewer");
    }

    private void seed(String username, String password, String role) {
        if (username.isBlank() || password.isBlank()) return;
        if (users.findByUsername(username).isPresent()) return;
        users.save(new AppUser(username, passwordEncoder.encode(password), role));
        log.info("seeded user {} ({})", username, role);
    }
}
