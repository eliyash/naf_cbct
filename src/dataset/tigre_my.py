import torch
import pickle
import numpy as np

from torch.utils.data import Dataset


class ConeGeometry(object):
    """
    Cone beam CT geometry. Note that we convert to meter from millimeter.
    """
    def __init__(self, data):

        # VARIABLE                                          DESCRIPTION                    UNITS
        # -------------------------------------------------------------------------------------
        self.DSD = data["DSD"]/1000 # Distance Source Detector      (m)
        self.DSO = data["DSO"]/1000  # Distance Source Origin        (m)
        # Detector parameters
        self.nDetector = np.flipud(np.array(data["nDetector"]))   # number of pixels              (px)
        self.dDetector = np.array(data["dDetector"])/1000  # size of each pixel            (m)
        self.sDetector = self.nDetector * self.dDetector  # total size of the detector    (m)
        # Image parameters
        self.nVoxel = np.array(data["nVoxel"])  # number of voxels              (vx)
        self.dVoxel = np.array(data["dVoxel"])/1000  # size of each voxel            (m)
        self.sVoxel = self.nVoxel * self.dVoxel  # total size of the image       (m)

        # Offsets
        self.offOrigin = np.array(data["offOrigin"])/1000  # Offset of image from origin   (m)
        self.offDetector = np.array(data["offDetector"])/1000  # Offset of Detector            (m)

        # Auxiliary
        self.accuracy = data["accuracy"]  # Accuracy of FWD proj          (vx/sample)  # noqa: E501
        # Mode
        self.mode = data["mode"]  # parallel, cone                ...
        self.filter = data["filter"]


class TIGREDatasetMy(Dataset):
    """
    TIGRE dataset.
    """
    def __init__(self, path, n_rays=1024, type="train", device="cuda"):    
        super().__init__()

        with open(path, "rb") as handle:
            ks, proj_geom, proj_data = pickle.load(handle)
        proj_data = proj_data.astype(float) / 4000

        self.geo = ConeGeometry(proj_geom)

        self.type = type
        self.n_rays = n_rays


        # if type == "train":
        self.projs = torch.tensor(proj_data, dtype=torch.float32, device=device)
        rays = self.get_rays(ks, self.geo, device)

        # self.rays = []
        # for i in range(len(ks)):
        near, far = self.get_near_far(len(ks) // 2, self.geo)
        self.rays = torch.cat([rays, torch.ones_like(rays[...,:1])*near, torch.ones_like(rays[...,:1])*far], dim=-1)

        self.n_samples = len(ks)
        coords = torch.stack(torch.meshgrid(torch.linspace(0, self.geo.nDetector[1] - 1, self.geo.nDetector[1], device=device),
                                            torch.linspace(0, self.geo.nDetector[0] - 1, self.geo.nDetector[0], device=device), indexing="ij"),
                             -1)
        self.coords = torch.reshape(coords, [-1, 2])
        self.image = torch.tensor(np.zeros((200, 300, 200)), dtype=torch.float32, device=device)
        self.voxels = torch.tensor(self.get_voxels(self.geo), dtype=torch.float32, device=device)
        # elif type == "val":
        #     self.projs = torch.tensor(data["val"]["projections"], dtype=torch.float32, device=device)
        #     angles = data["val"]["angles"]
        #     rays = self.get_rays(angles, self.geo, device)
        #     self.rays = torch.cat([rays, torch.ones_like(rays[...,:1])*self.near, torch.ones_like(rays[...,:1])*self.far], dim=-1)
        #     self.n_samples = data["numVal"]
        #     self.image = torch.tensor(data["image"], dtype=torch.float32, device=device)
        #     self.voxels = torch.tensor(self.get_voxels(self.geo), dtype=torch.float32, device=device)
        
    def __len__(self):
        return self.n_samples

    def __getitem__(self, index):
        # if self.type == "train":
        projs_valid = (self.projs[index]>0).flatten()
        coords_valid = self.coords[projs_valid]
        select_inds = np.random.choice(coords_valid.shape[0], size=[self.n_rays], replace=False)
        select_coords = coords_valid[select_inds].long()
        rays = self.rays[index, select_coords[:, 0], select_coords[:, 1]]
        # print('\n\n\n\n')
        # print(self.projs.shape, index, select_coords[:, 0], select_coords[:, 1])
        # print(select_coords[:, 0].min(), select_coords[:, 0].max())
        # print(select_coords[:, 1].min(), select_coords[:, 1].max())
        # print('\n\n\n\n')
        projs = self.projs[index, select_coords[:, 0], select_coords[:, 1]]
        out = {
            "projs":projs,
            "rays":rays,
        }
        # elif self.type == "val":
        #     rays = self.rays[index]
        #     projs = self.projs[index]
        #     out = {
        #         "projs":projs,
        #         "rays":rays,
        #     }
        return out

    def get_voxels(self, geo: ConeGeometry):
        """
        Get the voxels.
        """
        n1, n2, n3 = geo.nVoxel 
        s1, s2, s3 = geo.sVoxel / 2 - geo.dVoxel / 2

        xyz = np.meshgrid(np.linspace(-s1, s1, n1),
                        np.linspace(-s2, s2, n2),
                        np.linspace(-s3, s3, n3), indexing="ij")
        voxel = np.asarray(xyz).transpose([1, 2, 3, 0])
        return voxel
    
    def get_rays(self, ks, geo: ConeGeometry, device):
        """
        Get rays given one angle and x-ray machine geometry.
        """

        W, H = geo.nDetector
        DSD = geo.DSD
        rays = []

        for ind, k in enumerate(ks):
            offDetector = geo.offDetector[ind]
            pose = torch.Tensor(k).to(device)
            rays_o, rays_d = None, None
            if geo.mode == "cone":
                i, j = torch.meshgrid(torch.linspace(0, W - 1, W, device=device),
                                    torch.linspace(0, H - 1, H, device=device), indexing="ij")  # pytorch"s meshgrid has indexing="ij"
                uu = (i.t() + 0.5 - W / 2) * geo.dDetector[0] + offDetector[0]
                vv = (j.t() + 0.5 - H / 2) * geo.dDetector[1] + offDetector[1]
                dirs = torch.stack([uu / DSD, vv / DSD, torch.ones_like(uu)], -1)
                rays_d = torch.sum(torch.matmul(pose[:3,:3], dirs[..., None]).to(device), -1) # pose[:3, :3] * 
                rays_o = pose[:3, -1].expand(rays_d.shape)

            rays.append(torch.concat([rays_o, rays_d], dim=-1))

        return torch.stack(rays, dim=0)

    def angle2pose(self, DSO, angle):
        phi1 = -np.pi / 2
        R1 = np.array([[1.0, 0.0, 0.0],
                    [0.0, np.cos(phi1), -np.sin(phi1)],
                    [0.0, np.sin(phi1), np.cos(phi1)]])
        phi2 = np.pi / 2
        R2 = np.array([[np.cos(phi2), -np.sin(phi2), 0.0],
                    [np.sin(phi2), np.cos(phi2), 0.0],
                    [0.0, 0.0, 1.0]])
        R3 = np.array([[np.cos(angle), -np.sin(angle), 0.0],
                    [np.sin(angle), np.cos(angle), 0.0],
                    [0.0, 0.0, 1.0]])
        rot = np.dot(np.dot(R3, R2), R1)
        trans = np.array([DSO * np.cos(angle), DSO * np.sin(angle), 0])
        T = np.eye(4)
        T[:-1, :-1] = rot
        T[:-1, -1] = trans
        return T

    def get_near_far(self, ind: int, geo: ConeGeometry, tolerance=0.005):
        """
        Compute the near and far threshold.
        """
        DSO = geo.DSO[ind]
        offOrigin = geo.offOrigin[ind]
        dist1 = np.linalg.norm([offOrigin[0] - geo.sVoxel[0] / 2, offOrigin[1] - geo.sVoxel[1] / 2])
        dist2 = np.linalg.norm([offOrigin[0] - geo.sVoxel[0] / 2, offOrigin[1] + geo.sVoxel[1] / 2])
        dist3 = np.linalg.norm([offOrigin[0] + geo.sVoxel[0] / 2, offOrigin[1] - geo.sVoxel[1] / 2])
        dist4 = np.linalg.norm([offOrigin[0] + geo.sVoxel[0] / 2, offOrigin[1] + geo.sVoxel[1] / 2])
        dist_max = np.max([dist1, dist2, dist3, dist4])
        near = np.max([0, DSO - dist_max - tolerance])
        far = np.min([DSO * 2, DSO + dist_max + tolerance])
        return near, far
