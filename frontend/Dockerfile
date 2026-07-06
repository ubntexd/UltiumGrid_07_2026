FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/index.html /usr/share/nginx/html/index.html
COPY frontend/egaliseur.html /usr/share/nginx/html/egaliseur.html
COPY frontend/styles.css /usr/share/nginx/html/styles.css
COPY frontend/app.js /usr/share/nginx/html/app.js
COPY frontend/egaliseur.js /usr/share/nginx/html/egaliseur.js
EXPOSE 80
