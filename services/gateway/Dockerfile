FROM maven:3.9-eclipse-temurin-21 AS build
ENV MAVEN_OPTS="-Djava.net.preferIPv6Addresses=true"
WORKDIR /build
COPY maven-settings.xml /root/.m2/settings.xml
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src/ ./src/
RUN mvn -q -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app
COPY --from=build --chown=app:app /build/target/*.jar app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
