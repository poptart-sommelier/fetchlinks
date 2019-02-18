# Below are the commands needed to launch a container and modify the users:
###########################################################################
# Pull the docker image
# $ docker pull mysql/mysql-server:tag
# Start the docker container
# $ docker run -p 33600:3306 --name=mysql1 -d mysql/mysql-server:latest
# below will show the generated root password
# $ docker logs mysql1 2>&1 | grep GENERATED
# login to the container with the above password
# $ docker exec -it mysql1 mysql -uroot -p
# change root password
# $ ALTER USER 'root'@'localhost' IDENTIFIED BY 'averycomplexpassword111';
# set up user
# $ CREATE USER 'rich'@'%' IDENTIFIED BY 'testpassword';
# $ ALTER USER 'rich'@'%' IDENTIFIED WITH mysql_native_password BY 'testpassword'
# $ GRANT ALL PRIVILEGES ON *.* to 'rich'@'%' WITH GRANT OPTION;
# Now run the script below to set up the database

import MySQLdb

# Instantiate connection to mysql
db = MySQLdb.connect(host="127.0.0.1", port=33600, user="rich", passwd="testpassword")
cursor = db.cursor()

# Create fetchlinks DB
sql_command = 'CREATE DATABASE IF NOT EXISTS fetchlinks'
cursor.execute(sql_command)

# Create table 'links'
cursor.execute("USE fetchlinks")
cursor.execute("CREATE TABLE IF NOT EXISTS links (title varchar(2000), description VARCHAR(2000), author VARCHAR(100), "
               "source VARCHAR(100), uid VARCHAR(100), urls VARCHAR(2000), direct_link VARCHAR(2000), "
               "date_created VARCHAR(100));")

cursor.execute("ALTER TABLE links CONVER TO CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;")

# Commit and close
db.commit()
db.close()
