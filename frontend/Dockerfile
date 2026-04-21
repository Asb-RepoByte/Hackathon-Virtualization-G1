FROM nginx:alpine
# Remplace le chemin source par celui trouvé juste au-dessus
COPY ./dist/frontend/browser /usr/share/nginx/html
COPY default.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80