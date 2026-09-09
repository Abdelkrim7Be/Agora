FROM maven:3-eclipse-temurin-26 AS build
ENV MAVEN_OPTS="-Djava.net.preferIPv4Stack=true"
WORKDIR /build
COPY maven-settings.xml /root/.m2/settings.xml
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src/ ./src/
RUN mvn -q -DskipTests package

FROM eclipse-temurin:25-jre-alpine
WORKDIR /app
RUN apk upgrade --no-cache
RUN addgroup -S app && adduser -S -G app -h /app app \
    && chown -R app:app /app
COPY --from=build --chown=app:app /build/target/*.jar app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
