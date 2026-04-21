FROM postgres:16-alpine

# Définir les variables d'environnement par défaut
ENV POSTGRES_DB=carbon
ENV POSTGRES_USER=carbon
ENV POSTGRES_PASSWORD=carbon

# Copier les scripts d'initialisation et les DATA
# Tout fichier .sql ou .csv dans ce dossier est exécuté/importé au premier lancement
COPY init.sql /docker-entrypoint-initdb.d/
COPY boavizta-data-us.csv /docker-entrypoint-initdb.d/
