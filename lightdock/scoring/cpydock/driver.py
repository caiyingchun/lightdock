"""Implementation of the pyDock scoring function.

C-implementation of the pyDock scoring function and using the freesasa library:
https://github.com/mittinatten/freesasa
"""

import numpy as np
import freesasa
from freesasa import Structure
from lightdock.scoring.functions import ScoringFunction, ModelAdapter
from lightdock.structure.model import DockingModel
import lightdock.scoring.cpydock.energy.c.cpydock as cpydock
import lightdock.scoring.cpydock.energy.parameters as parameters
from lightdock.util.logger import LoggingManager
import lightdock.scoring.cpydock.data.amber as amber
import lightdock.scoring.cpydock.data.vdw as vdw
import lightdock.scoring.cpydock.data.solvation as solvation

log = LoggingManager.get_logger('cpydock')
freesasa.setVerbosity(freesasa.silent)


class CPyDockModel(DockingModel):
    """Prepares the structure necessary for the C-implementation of the pyDock scoring function"""
    def __init__(self, objects, coordinates, charges, vdw_energy, vdw_radii, des_energy, des_radii, sasa, hydrogens,
                 reference_points=None, n_modes=None):
        super(CPyDockModel, self).__init__(objects, coordinates, reference_points)
        self.charges = charges
        self.vdw_energy = vdw_energy
        self.vdw_radii = vdw_radii
        self.des_energy = des_energy
        self.des_radii = des_radii
        self.sasa = sasa
        self.hydrogens = hydrogens
        self.n_modes = n_modes

    def clone(self):
        """Creates a copy of the current model"""
        return CPyDockModel(self.objects, self.coordinates.copy(),
                            self.charges, self.vdw_energy, self.vdw_radii,
                            self.des_energy, self.des_radii, self.sasa, self.hydrogens,
                            reference_points=self.reference_points.copy())


class CPyDockAdapter(ModelAdapter):
    """Adapts a given Complex to a DockingModel object suitable for this
    PyDock scoring function.
    """
    def _get_docking_model(self, molecule):
        atoms = molecule.atoms
        # Assign properties to atoms
        for atom in atoms:
            res_name = atom.residue_name
            atom_name = atom.name
            if res_name == "HIS":
                res_name = 'HID'
            if atom_name in amber.translate:
                atom_name = amber.translate[atom.name]
            atom_id = "%s-%s" % (res_name, atom_name)
            atom.amber_type = amber.amber_types[atom_id]
            atom.charge = amber.charges[atom_id]
            atom.mass = amber.masses[atom.amber_type]
            atom.vdw_energy = vdw.vdw_energy[atom.amber_type]
            atom.vdw_radius = vdw.vdw_radii[atom.amber_type]

        # Prepare common model information
        elec_charges = np.array([atom.charge for atom in atoms])
        vdw_energies = np.array([atom.vdw_energy for atom in atoms])
        vdw_radii = np.array([atom.vdw_radius for atom in atoms])
        coordinates = molecule.copy_coordinates()
        des_energy, des_radii = solvation.get_solvation(molecule)

        # Calculate desolvation reference energy
        log.info('Calculating reference SASA...')
        structure = Structure()
        des_radii_no_H = []
        for i, atom in enumerate(atoms):
            if not atom.is_hydrogen():
                structure.addAtom(atom.name, atom.residue_name, atom.residue_number, atom.chain_id,
                                  atom.x, atom.y, atom.z)
                des_radii_no_H.append(des_radii[i])
        structure.setRadii(list(des_radii_no_H))
        sasa_result = freesasa.calc(structure)
        sasa = []
        j = 0
        for i, atom in enumerate(atoms):
            if not atom.is_hydrogen():
                sasa.append(sasa_result.atomArea(j))
                j += 1
            else:
                sasa.append(-1.0)
        sasa = np.array(sasa)
        hydrogens = np.array([0 if atom.is_hydrogen() else 1 for atom in atoms])
        log.info('Done.')

        reference_points = ModelAdapter.load_reference_points(molecule)

        try:
            return CPyDockModel(atoms, coordinates, elec_charges, vdw_energies, vdw_radii, des_energy, des_radii,
                                sasa, hydrogens, reference_points=reference_points, n_modes=molecule.n_modes.copy())
        except AttributeError:
            return CPyDockModel(atoms, coordinates, elec_charges, vdw_energies, vdw_radii, des_energy, des_radii,
                                sasa, hydrogens, reference_points=reference_points)


class CPyDock(ScoringFunction):
    def __init__(self, weight=1.0):
        super(CPyDock, self).__init__(weight)
        try:
            with open(parameters.vdw_input_file) as vdw_file:
                self.scoring_vdw_weight = float(vdw_file.readline())
        except (IOError, ValueError) as e:
            log.warning('Error (%s), using default VDW cutoff' % str(e))
            self.scoring_vdw_weight = parameters.scoring_vdw_weight
        log.info('PyDock VDW cutoff is: %3.2f' % self.scoring_vdw_weight)

    def __call__(self, receptor, receptor_coordinates, ligand, ligand_coordinates):
        """Computes the pyDock scoring energy using receptor and ligand which are
        instances of DockinModel.
        """
        elec, vdw, solv_rec, solv_lig = cpydock.calculate_energy(receptor_coordinates, ligand_coordinates,
                                                                 receptor.charges, ligand.charges,
                                                                 receptor.vdw_energy, ligand.vdw_energy,
                                                                 receptor.vdw_radii, ligand.vdw_radii,
                                                                 receptor.hydrogens, ligand.hydrogens,
                                                                 receptor.sasa, ligand.sasa,
                                                                 receptor.des_energy, ligand.des_energy)
        solv = -1*(solv_rec + solv_lig)
        return (elec + parameters.scoring_vdw_weight * vdw + solv)*-1.


# Needed to dynamically load the scoring functions from command line
DefinedScoringFunction = CPyDock
DefinedModelAdapter = CPyDockAdapter