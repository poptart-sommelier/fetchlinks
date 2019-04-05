docker build -t fetchlinks-mysql .
docker run -d -p 3306:3306 --name fetchlinks-mysql -e MYSQL_ROOT_PASSWORD=thepassword fetchlinks-mysql

