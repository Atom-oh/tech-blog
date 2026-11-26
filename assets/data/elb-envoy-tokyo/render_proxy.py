"""Render an ELB for Envoy CloudFormation template with file-based xDS."""
import argparse
import base64
import io
import ipaddress
import json
import zipfile

TYPE = "type.googleapis.com/"
LOCAL = "envoy.filters.http.local_ratelimit"
BUCKET = TYPE + "envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit"


def source(name):
    return {"resource_api_version": "V3", "path_config_source": {
        "path": f"/xds/{name}.yaml", "watched_directory": {"path": "/xds"}}}


def cluster(name, address):
    hosts = []
    if address:
        host = {"socket_address": {"address": str(ipaddress.IPv4Address(address)), "port_value": 8080}}
        hosts.append({"lb_endpoints": [{"endpoint": {"address": host}}]})
    return {"@type": TYPE + "envoy.config.cluster.v3.Cluster", "name": name,
            "type": "STATIC", "connect_timeout": "2s",
            "load_assignment": {"cluster_name": name, "endpoints": hosts}}


def routes(revision):
    def fixed(path, status, body):
        return {"match": {"path": path}, "direct_response": {
            "status": status, "body": {"inline_string": body}}}

    def forward(prefix, backend):
        return {"match": {"prefix": prefix}, "route": {"cluster": backend, "prefix_rewrite": "/"}}

    limit = forward("/limited/", "ec2")
    always = {"default_value": {"numerator": 100, "denominator": "HUNDRED"}}
    limit["typed_per_filter_config"] = {LOCAL: {
        "@type": BUCKET, "stat_prefix": "limited", "token_bucket": {
            "max_tokens": 2, "tokens_per_fill": 2, "fill_interval": "60s"},
        "filter_enabled": always, "filter_enforced": always,
        "response_headers_to_add": [{"header": {"key": "x-lab-limit", "value": "local"}}]}}
    weighted = {"match": {"prefix": "/split/"}, "route": {"weighted_clusters": {"clusters": [
        {"name": "ec2", "weight": 80}, {"name": "ecs", "weight": 20}]}}}
    rules = [fixed("/readyz", 200, "ready"), fixed("/revision", 200, revision), limit, weighted]
    rules.extend(forward(f"/{name}/", name) for name in ("eks", "ec2", "ecs"))
    rules.append({"match": {"prefix": "/"}, "direct_response": {"status": 404}})
    return {"@type": TYPE + "envoy.config.route.v3.RouteConfiguration", "name": "ingress",
            "virtual_hosts": [{"name": "backends", "domains": ["*"], "routes": rules}]}


def bootstrap():
    manager = {"@type": TYPE + "envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
               "stat_prefix": "ingress", "rds": {"route_config_name": "ingress", "config_source": source("routes")},
               "http_filters": [{"name": LOCAL, "typed_config": {"@type": BUCKET, "stat_prefix": "local"}},
                                {"name": "envoy.filters.http.router", "typed_config": {
                                    "@type": TYPE + "envoy.extensions.filters.http.router.v3.Router"}}]}
    listener = {"name": "ingress", "address": {"socket_address": {"address": "0.0.0.0", "port_value": 8080}},
                "filter_chains": [{"filters": [{"name": "envoy.filters.network.http_connection_manager",
                                                "typed_config": manager}]}]}
    return {"static_resources": {"listeners": [listener]}, "dynamic_resources": {"cds_config": source("clusters")}}


def resource_bundle(groups, revision):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, resources in groups.items():
            data = {"version_info": revision, "resources": resources}
            # Stable timestamps keep an identical snapshot byte-for-byte reproducible.
            entry = zipfile.ZipInfo(f"xds/{name}.yaml", (2026, 1, 1, 0, 0, 0))
            archive.writestr(entry, json.dumps(data).encode(), compress_type=zipfile.ZIP_DEFLATED)
    return base64.b64encode(buffer.getvalue()).decode()


def template(args):
    groups = {"routes": [routes(args.revision)], "clusters": [
        cluster(name, getattr(args, name)) for name in ("eks", "ec2", "ecs")]}
    trusted = {"Effect": "Allow", "Principal": {"Service": "elasticloadbalancing.amazonaws.com"}, "Action": "sts:AssumeRole"}
    log_resource = {"Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/elb-proxy/${AWS::StackName}*"}
    role = {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [trusted]},
        "Policies": [{"PolicyName": "ProxyLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": log_resource}]}}]}}
    resources = {"Role": role, "Logs": {"Type": "AWS::Logs::LogGroup", "Properties": {
        "LogGroupName": {"Fn::Sub": "/aws/elb-proxy/${AWS::StackName}"}, "RetentionInDays": 3}},
        "FromNlb": {"Type": "AWS::EC2::SecurityGroupIngress", "Properties": {
            "GroupId": {"Ref": "ProxySG"}, "SourceSecurityGroupId": {"Ref": "NlbSG"},
            "IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080}},
        "Proxy": {"Type": "AWS::ElasticLoadBalancingProxy::Proxy", "DependsOn": ["Logs"], "Properties": {
            "Name": {"Ref": "AWS::StackName"}, "Type": "envoy", "ProxyVersion": "1.40",
            "RoleArn": {"Fn::GetAtt": ["Role", "Arn"]}, "SecurityGroups": [{"Ref": "ProxySG"}],
            "Subnets": [{"Ref": "PrivateA"}, {"Ref": "PrivateB"}],
            "IpAddressingConfiguration": {"Ipv4Addressability": "internal", "Ipv6Addressability": "off"},
            "ConfigurationFile": {"ConfigurationBytes": {"Fn::Base64": json.dumps(bootstrap())}},
            "ConfigurationArchive": {"ConfigurationBytes": resource_bundle(groups, args.revision)}}}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Parameters": {
        key: {"Type": "AWS::EC2::" + ("Subnet::Id" if key.startswith("Private") else "SecurityGroup::Id")}
        for key in ("PrivateA", "PrivateB", "ProxySG", "NlbSG")}, "Resources": resources,
        "Outputs": {"DNS": {"Value": {"Fn::GetAtt": ["Proxy", "DnsName"]}}}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for backend in ("eks", "ec2", "ecs"):
        parser.add_argument("--" + backend, help="private IPv4 address of the backend")
    parser.add_argument("--revision", default="v1")
    print(json.dumps(template(parser.parse_args()), indent=2))
