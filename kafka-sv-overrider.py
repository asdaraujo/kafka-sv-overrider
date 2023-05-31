from datetime import datetime
from getpass import getpass
from optparse import OptionParser
import cm_client
import json
import os
import re
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get_parser():
    opt_parser = OptionParser(usage='%prog [options] <host>')
    opt_parser.add_option('--cm-url', action='store', dest='cm_url',
                          help='Cloudera Manager URL.')
    opt_parser.add_option('--template', action='store', dest='template',
                          help='Cluster template file.')
    opt_parser.add_option('--username', action='store', dest='username',
                          help='Cloudera Manager username.', type='string', default=None)
    opt_parser.add_option('--password', action='store', dest='password',
                          help='Cloudera Manager password.', type='string', default=None)
    opt_parser.add_option('--tls-ca-cert', action='store', dest='tls_ca_cert', default=None,
                          help='TLS truststore.')
    opt_parser.add_option('--cluster-name', action='store', dest='cluster_name', default=None,
                          help='Cluster name.')
    opt_parser.add_option('--service-name', action='store', dest='service_name', default=None,
                          help='Service name.')
    opt_parser.add_option('--backup-file', action='store', dest='backup_file', default=None,
                          help='Backup dir.')
    opt_parser.add_option('--restore', action='store_true', dest='restore',
                          help='Restore config.')
    opt_parser.add_option('--override', action='store_true', dest='override',
                          help='Override config.')
    return opt_parser


class KafkaBrokerSafetyValveOverrider(object):

    def __init__(self):
        self.options = None
        self._api_client = None
        self._cluster_api = None
        self._service_api = None
        self._role_api = None
        self._rcg_api = None
        self.OVERRIDE = 'OVERRIDE'
        self.RESTORE = 'RESTORE'
        self._PROPERTY = 'kafka.properties_role_safety_valve'

    def _ensure_parsed_options(self):
        if not self.options:
            (self.options, _) = _get_parser().parse_args()

    def _get_api_version(self):
        resp = requests.get("{}/api/version".format(self.cm_url), verify=False, auth=(self.username, self.password))
        if resp.status_code == 200 and resp.text:
            return resp.text
        else:
            raise RuntimeError('Failed to retrieve API version. Response:{}'.format(resp))

    @property
    def api_client(self):
        if self._api_client is None:
            cm_client.configuration.username = self.username
            cm_client.configuration.password = self.password
            cm_client.configuration.ssl_ca_cert = self.tls_ca_cert
            self._api_client = cm_client.ApiClient(self.cm_url + '/api/' + self._get_api_version())
        return self._api_client

    @property
    def cluster_api(self):
        if self._cluster_api is None:
            self._cluster_api = cm_client.ClustersResourceApi(self.api_client)
        return self._cluster_api

    @property
    def service_api(self):
        if self._service_api is None:
            self._service_api = cm_client.ServicesResourceApi(self.api_client)
        return self._service_api

    @property
    def role_api(self):
        if self._role_api is None:
            self._role_api = cm_client.RolesResourceApi(self.api_client)
        return self._role_api

    @property
    def rcg_api(self):
        if self._rcg_api is None:
            self._rcg_api = cm_client.RoleConfigGroupsResourceApi(self.api_client)
        return self._rcg_api

    def get_clusters(self):
        return self.cluster_api.read_clusters().items

    def get_kafka_services(self):
        return [s for s in self.service_api.read_services(self.cluster_name).items if s.type == 'KAFKA']

    def get_kafka_roles(self):
        return [r for r in self.role_api.read_roles(self.cluster_name, self.service_name).items if
                r.type == 'KAFKA_BROKER']

    def get_rcg_config(self, rcg_name, property_name):
        for r in self.rcg_api.read_config(self.cluster_name, rcg_name, self.service_name, view='summary').items:
            if r.name == property_name:
                return r

    def get_role_config(self, role_name, property_name):
        for r in self.role_api.read_role_config(self.cluster_name, role_name, self.service_name, view='summary').items:
            if r.name == property_name:
                return r

    def update_kafka_role_config(self, role_name, property_name, value):
        body = cm_client.models.ApiConfigList([cm_client.models.ApiConfig(name=property_name, value=value)])
        message = "Updated by KafkaBrokerSafetyValveOverrider at {}.".format(
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.role_api.update_role_config(self.cluster_name, role_name, self.service_name, message=message, body=body)

    @property
    def cm_url(self):
        self._ensure_parsed_options()
        # validate and sanitize CM URL
        if not self.options.cm_url:
            raise RuntimeError("--cm-url is a required argument.")
        m = re.match(r'(https?://[a-z0-9.-]+:[0-9]+).*', self.options.cm_url)
        if m:
            return m.groups()[0]
        else:
            raise RuntimeError("CM URL must have the format: https://hostname:port")

    @property
    def tls_ca_cert(self):
        self._ensure_parsed_options()
        # validate and sanitize truststore
        if self.options.tls_ca_cert is None or os.path.exists(self.options.tls_ca_cert):
            return self.options.tls_ca_cert
        else:
            raise RuntimeError("Truststore {} does not exist.".format(self.options.tls_ca_cert))

    @property
    def username(self):
        self._ensure_parsed_options()
        # validate and sanitize username
        if not self.options.username:
            raise RuntimeError("--username is a required argument.")
        return self.options.username

    @property
    def password(self):
        self._ensure_parsed_options()
        # validate and sanitize username
        if not self.options.password:
            self.options.password = getpass('Password: ')
        return self.options.password

    @property
    def cluster_name(self):
        self._ensure_parsed_options()
        # validate and sanitize cluster name
        valid_clusters = [c.name for c in self.get_clusters()]
        if not self.options.cluster_name or self.options.cluster_name not in valid_clusters:
            raise RuntimeError(
                "--cluster-name is required and must be one of these: {}.".format(', '.join(valid_clusters)))
        return self.options.cluster_name

    @property
    def service_name(self):
        self._ensure_parsed_options()
        # validate and sanitize service name
        valid_services = [c.name for c in self.get_kafka_services()]
        if not self.options.service_name or self.options.service_name not in valid_services:
            raise RuntimeError(
                "--service-name is required and must be one of these: {}.".format(', '.join(valid_services)))
        return self.options.service_name

    @property
    def template(self):
        self._ensure_parsed_options()
        # validate and sanitize the template parameter
        if not self.options.template:
            raise RuntimeError("--template is a required argument.")
        elif not os.path.exists(self.options.template):
            raise RuntimeError("Template file {} does not exist.".format(self.options.template))
        else:
            return self.options.template

    @property
    def backup_file(self):
        self._ensure_parsed_options()
        # validate and sanitize the backup_file parameter
        if not self.options.backup_file:
            raise RuntimeError("--backup-file is a required argument.")
        elif os.path.exists(self.options.backup_file):
            if os.path.isdir(self.options.backup_file):
                raise RuntimeError("Backup file {} cannot be a directory.".format(self.options.backup_file))
            elif os.stat(self.options.backup_file).st_size > 0 and self.action == self.OVERRIDE:
                raise RuntimeError("Backup file {} is not empty.".format(self.options.backup_file))
        return self.options.backup_file

    @property
    def action(self):
        self._ensure_parsed_options()
        # validate and sanitize the action
        if self.options.override and self.options.restore:
            raise RuntimeError("--override and --restore are mutually exclusive arguments.")
        elif not self.options.override and not self.options.restore:
            raise RuntimeError("You must specify either --override or --restore.")
        elif self.options.override:
            return self.OVERRIDE
        else:
            return self.RESTORE

    def render_template(self, hostname):
        return re.sub('HOSTNAME', hostname, open(self.template, 'r').read())

    def backup_config(self):
        backup = {}
        for role in self.get_kafka_roles():
            hostname = role.host_ref.hostname
            config = self.get_role_config(role.name, self._PROPERTY)
            if hostname not in backup:
                backup[hostname] = {}
            backup[hostname][self._PROPERTY] = config.value
        json.dump(backup, open(self.backup_file, 'w'), indent=2)

    def override(self):
        self.backup_config()
        for role in self.get_kafka_roles():
            hostname = role.host_ref.hostname
            self.update_kafka_role_config(role.name, self._PROPERTY, self.render_template(hostname))
            print('Property {} of role {} on host {} has been overriden.'.format(self._PROPERTY, role.name, hostname))

    def restore(self):
        backup = json.load(open(self.backup_file, 'r'))
        for role in self.get_kafka_roles():
            hostname = role.host_ref.hostname
            if hostname in backup:
                for key, value in backup[hostname].items():
                    rcg_value = self.get_rcg_config(role.role_config_group_ref.role_config_group_name, key).value
                    if value == rcg_value:
                        try:
                            self.update_kafka_role_config(role.name, key, None)
                        except cm_client.rest.ApiException as exc:
                            if 'Could not find config to delete' not in exc.body:
                                raise exc
                        print('Override for property {} of role {} on host {}'
                              ' has been removed.'.format(key, role.name, hostname))
                    else:
                        self.update_kafka_role_config(role.name, key, value)
                        print(
                            'Override for property {} of role {} on host {}'
                            ' has been restored to previous value.'.format(key, role.name, hostname))

    def run(self):
        if self.action == self.OVERRIDE:
            self.override()
        elif self.action == self.RESTORE:
            self.restore()


if __name__ == '__main__':
    overrider = KafkaBrokerSafetyValveOverrider()
    overrider.run()
