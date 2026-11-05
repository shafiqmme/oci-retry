"""Keep asking Oracle Cloud for an Always Free server until it has room (Singapore is often "out of capacity").
Tries the Ampere A1 server (2 OCPU, 12 GB) first; if there is no server yet, also tries the small AMD micro.
Reads the OCI API key from ~/.oci (written by the workflow from secrets). Prints FOUND <shape> <ip> on success."""
import os, sys, time
import oci

PUBKEY = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_key.pub")).read().strip()  # public half of the server SSH key (safe to publish)
cfg = oci.config.from_file(os.path.expanduser("~/.oci/config"))
tenancy = cfg["tenancy"]
compute, net, ident = oci.core.ComputeClient(cfg), oci.core.VirtualNetworkClient(cfg), oci.identity.IdentityClient(cfg)
A1, MICRO = "VM.Standard.A1.Flex", "VM.Standard.E2.1.Micro"


def live():
    return [i for i in compute.list_instances(tenancy).data if i.lifecycle_state not in ("TERMINATED", "TERMINATING")]


def public_ip(inst):
    for a in compute.list_vnic_attachments(tenancy, instance_id=inst.id).data:
        v = net.get_vnic(a.vnic_id).data
        if v.public_ip:
            return v.public_ip
    return "?"


def subnet_id():
    subs = [s for s in net.list_subnets(tenancy).data if not s.prohibit_public_ip_on_vnic and s.lifecycle_state == "AVAILABLE"]
    if not subs:
        sys.exit("NO_PUBLIC_SUBNET: create a VCN with the 'VCN with Internet Connectivity' wizard first")
    return subs[0].id


def image_id(shape):
    imgs = compute.list_images(tenancy, operating_system="Canonical Ubuntu", operating_system_version="24.04", shape=shape,
                               sort_by="TIMECREATED", sort_order="DESC").data
    return [i for i in imgs if "Minimal" not in (i.display_name or "")][0].id


def launch(shape, ad, sub):
    d = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad, compartment_id=tenancy, shape=shape, display_name="website",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=2, memory_in_gbs=12) if shape == A1 else None,
        create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=sub, assign_public_ip=True),
        metadata={"ssh_authorized_keys": PUBKEY},
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image_id(shape), boot_volume_size_in_gbs=50))
    return compute.launch_instance(d).data


have = {i.shape: i for i in live()}
if A1 in have:
    print("FOUND", A1, public_ip(have[A1])); sys.exit(0)
ad = ident.list_availability_domains(tenancy).data[0].name
sub = subnet_id()
end = time.time() + int(os.environ.get("TRY_SECONDS", "240"))
while time.time() < end:
    for shape in ([A1] if have else [A1, MICRO]):
        try:
            inst = launch(shape, ad, sub)
            print("LAUNCHED", shape, inst.id, flush=True)
            for _ in range(40):
                time.sleep(15)
                if compute.get_instance(inst.id).data.lifecycle_state == "RUNNING":
                    break
            print("FOUND", shape, public_ip(compute.get_instance(inst.id).data)); sys.exit(0)
        except oci.exceptions.ServiceError as e:
            print(time.strftime("%H:%M:%S"), shape, e.status, (e.message or "")[:90], flush=True)
            time.sleep(40 if e.status == 429 else 10)  # stay under Oracle's request limit
    time.sleep(60)
print("NOT_YET")
