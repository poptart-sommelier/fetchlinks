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


<H3>Clone fetchlinks_webapp repo</H3>
git clone https://github.com/poptart-sommelier/fetchlinks_webapp.git<br>

<H3>Install Requirements For fetchlinks-webapp</H3>

<H3>Configure mysql DBs and Tables</H3>










