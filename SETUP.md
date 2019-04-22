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

<H3>Install Condas</H3>
browse to https://www.anaconda.com/distribution/#download-section<br>
wget (most_recent_package.sh)<br>
bash (most_recent_package.sh)<br>
conda update conda<br>

<H3>Create Conda Environments</H3>
conda create -n fetchlinks python=3.7 anaconda<br>
conda create -n fetchlinks_webapp python=3.7 anaconda<br>

<H3>Clone fetchlinks repo</H3>
git clone https://github.com/poptart-sommelier/fetchlinks.git<br>

<H3>Install Requirements For fetchlinks</H3>
conda activate fetchlinks<br>
while read requirement; do conda install --yes $requirement; done < requirements.txt<br>
pip install python_dateutil<br>
pip install requests_oauthlib<br>

<H3>Clone fetchlinks_webapp repo</H3>
git clone https://github.com/poptart-sommelier/fetchlinks_webapp.git<br>

<H3>Install Requirements For fetchlinks-webapp</H3>
while read requirement; do conda install --yes $requirement; done < requirements.txt<br>
pip install flask_sqlalchemy<br>

<H3>Configure Database And Users</H3>
mysql -u root -p < fetchlinks/docker_sql/sql-scripts/createtable.sql<br>
configure root for login, localhost only

<H3>Configure Secrets</H3>
copy them to location defined in config.

<H3>Configure Cronjob On Server</H3>
0 * * * * /home/rich/anaconda3/envs/fetchlinks/bin/python3 /home/rich/scripts/fetchlinks/fetch_links.py<br>











