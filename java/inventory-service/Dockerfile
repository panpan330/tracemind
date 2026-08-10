# inventory-service 构建(上下文 = java/,dockerfile: inventory-service/Dockerfile)
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY common ./common
COPY inventory-service ./inventory-service
RUN mvn -q -pl inventory-service -am package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /src/inventory-service/target/inventory-service-*.jar app.jar
ENV JAVA_OPTS="-Xmx256m -XX:+UseSerialGC"
EXPOSE 8082
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
