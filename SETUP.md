<H1>Installation and Configuration</H1>

<H2>Update, Upgrade & Configure System Updates</H2>
sudo apt-get update && apt-get upgrade<br>
!!! CONFIGURE SYSTEM FOR AUTOMATIC UPDATES !!!<br>
!!! ENSURE NO SSH WITHOUT SSH KEY !!!<br>

<H2>Mysql and Python</H2>
<H3>Install python3, mysql:</H3>
sudo apt install python3, python3-venv, mysql-server<br>

<H3>Configure mysql server:</H3>
sudo mysql_secure_installation<br>

<H3>Clone fetchlinks repo</H3>
git clone https://github.com/poptart-sommelier/fetchlinks.git<br>

<H3>Install Requirements For fetchlinks</H3>
while read requirement; do pip3 install --yes $requirement; done < requirements.txt<br>
pip install python_dateutil<br>
pip install requests_oauthlib<br>

<H3>Clone fetchlinks_webapp repo</H3>
git clone https://github.com/poptart-sommelier/fetchlinks_webapp.git<br>

<H3>Install Requirements For fetchlinks-webapp</H3>
while read requirement; do pip3 install --yes $requirement; done < requirements.txt<br>
pip install flask_sqlalchemy<br>

<H3>Configure Database And Users</H3>
mysql -u root -p < fetchlinks/docker_sql/sql-scripts/createtable.sql<br>
configure root for login, localhost only

<H3>Configure Secrets</H3>
copy them to location defined in config.

<H3>Configure Cronjob On Server</H3>
0 * * * * cd /home/rich_donaghy/fetchlinks && /usr/bin/python3 /home/rich_donaghy/fetchlinks/fetch_links.py

<H3>Set up service</H3>
#TO RUN ON PORT 80 (usually nothing can bind to port 80 unless running as root, we don't want that) 
#The specific python binary must be specified, not the symlinked generic "python3", it must point to the #binary 
#ls –lah /usr/bin/python3.6 <-- this will show that it is a binary and not a symlink 
sudo setcap CAP_NET_BIND_SERVICE=+eip /usr/bin/python3.6 

Needs to be configured as a systemctl service 
Create this file:  
/etc/systemd/system/fetchlinks_webapp.service 

[Unit] 
Description=fetchlinks web application 
After=network.target 

[Service] 
User=rich 
WorkingDirectory=/home/rich/scripts/fetchlinks_webapp/ 
ExecStart=/home/rich/anaconda3/envs/fetchlinks_webapp/bin/python3 -m flask run --host=0.0.0.0 --port=80 
Restart=always 

[Install] 
WantedBy=multi-user.target 

# THESE MUST BE RUN AS ROOT 
Then run: 
journalctl -u fetchlinks_webapp.service 

To start the service after a reboot: 
systemctl start fetchlinks_webapp.service 

