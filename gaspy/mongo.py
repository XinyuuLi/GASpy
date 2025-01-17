'''
Note that this entire module was simply copied from John Kitchin's
vasp.mongo for use here. We used his code and put it here to solve
dependency issues. All credit goes to him, and we thank him for his help.

This module will be like the ase-db but different in the following ways:
1. Booleans are stored as booleans.
2. There is no numeric id.
3. Tags are stored in an array.
'''

__authors__ = ['John Kitchin', 'Kevin Tran']
__email__ = 'ktran@andrew.cmu.edu'

import os
from collections import OrderedDict
import datetime
import json
import spglib
import numpy as np
from ase import Atoms, Atom
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io.jsonio import encode
from ase.constraints import dict2constraint


def make_doc_from_atoms(atoms, **kwargs):
    '''
    Creates a Mongo document (i.e., dictionary/json) for pushing into
    a Mongo collection.

    Args:
        atoms   ase.Atoms object
        kwargs  Key-value pairs that you want to add  to the document
                in addition to what's normally added
    Returns:
        doc A dictionary with the standard subdocuments:
            atoms       See the `make_atoms_dict` function.
            calculator  Generated by the `calculator.todict` method
            results     Some information that we automatically parse
                        out from relxations like energy, forces, and stress
            user        Whicher user did the relaxation
            ctime       datetime object corresponding to the time the document was made
            mtime       datetime object corresponding to when the document was last updated
            kwargs      Other key-value pairs will be generated
                        according to the user-supplied kwargs
    '''
    doc = OrderedDict()

    atoms_dict = OrderedDict(_make_atoms_dict(atoms))
    calc_dict = _make_calculator_dict(atoms)
    results_dict = _make_results_dict(atoms)
    doc.update({'atoms': atoms_dict})
    doc.update({'calc': calc_dict})
    doc.update({'results': results_dict})

    doc['user'] = os.getenv('USER')
    doc['ctime'] = datetime.datetime.utcnow()
    doc['mtime'] = datetime.datetime.utcnow()

    doc.update(kwargs)

    return doc


def _make_atoms_dict(atoms):
    '''
    Convert an ase.Atoms object into a dictionary for json storage.

    Arg:
        atoms   ase.Atoms object
    Returns:
        atoms_dict  A dictionary with various atoms information stored
    '''
    # If the atoms object is relaxed, then get the magnetic moments from the
    # calculator. We do this because magnetic moments of individual atoms
    # within a structure are mutable and actually change when the atom is
    # pulled from the structure (even inside a list comprehension).
    try:
        magmoms = atoms.get_magnetic_moments()
        atoms_dict = OrderedDict(atoms=[{'symbol': atom.symbol,
                                         'position': json.loads(encode(atom.position)),
                                         'tag': atom.tag,
                                         'index': atom.index,
                                         'charge': atom.charge,
                                         'momentum': json.loads(encode(atom.momentum)),
                                         'magmom': magmoms[i]}
                                        for i, atom in enumerate(atoms)],
                                 cell=atoms.cell,
                                 pbc=atoms.pbc,
                                 info=atoms.info,
                                 constraints=[c.todict() for c in atoms.constraints])

    # If the atoms object is unrelaxed, then get the magnetic moment from the
    # individual atom
    except RuntimeError:
        atoms_dict = OrderedDict(atoms=[{'symbol': atom.symbol,
                                         'position': json.loads(encode(atom.position)),
                                         'tag': atom.tag,
                                         'index': atom.index,
                                         'charge': atom.charge,
                                         'momentum': json.loads(encode(atom.momentum)),
                                         'magmom': atom.magmom}
                                        for atom in atoms],
                                 cell=atoms.cell,
                                 pbc=atoms.pbc,
                                 info=atoms.info,
                                 constraints=[c.todict() for c in atoms.constraints])

    # Redundant information for search convenience.
    atoms_dict['natoms'] = len(atoms)
    cell = atoms.get_cell()
    atoms_dict['mass'] = sum(atoms.get_masses())
    syms = atoms.get_chemical_symbols()
    atoms_dict['spacegroup'] = spglib.get_spacegroup(make_spglib_cell_from_atoms(atoms))
    atoms_dict['chemical_symbols'] = list(set(syms))
    atoms_dict['symbol_counts'] = {sym: syms.count(sym) for sym in syms}
    if cell is not None and np.linalg.det(cell) > 0:
        atoms_dict['volume'] = atoms.get_volume()

    return json.loads(encode(atoms_dict))


def make_spglib_cell_from_atoms(atoms):
    '''
    `spglib` uses `cell` tuples to do things, but we normally work with
    `ase.Atoms` objects. This function contains a snippet from spglib itself
    that converts an `ase.Atoms` object into a `cell` tuple.

    Arg:
        atoms   Instance of an `ase.Atoms` object
    Returns:
        cell    A 3-tuple that `spglib` can use to perform various operations
    '''
    lattice = np.array(atoms.get_cell().T, dtype='double', order='C')
    positions = np.array(atoms.get_scaled_positions(),
                         dtype='double', order='C')
    numbers = np.array(atoms.get_atomic_numbers(), dtype='intc')
    cell = (lattice, positions, numbers)
    return cell


def _make_calculator_dict(atoms):
    '''
    Create a dictionary from an ase.Atoms' object's `calculator` attribute

    Arg:
        atoms   ase.Atoms object
    Returns:
        calc_dict   A dictionary with various calculator information stored.
                    Returns an empty dictionary if there is no calculator.
    '''
    calc_dict = OrderedDict()
    calculator = atoms.get_calculator()

    if calculator:
        try:
            calc_dict['calculator'] = calculator.todict()
            # Convert the kpts into a list of integers instead of an array of numpy64's
            # so that Mongo can encode it. EAFP in case there is no calculator
            try:
                calc_dict['calculator']['kpts'] = [int(kpt) for kpt in calc_dict['calculator']['kpts']]
            except KeyError:
                pass
        except AttributeError:
            calc_dict['calculator'] = {}

        # This might make it easier to reload these later. I
        # believe you import the class from the module then create
        # an instance of the class.
        calc_dict['calculator']['module'] = calculator.__module__
        calc_dict['calculator']['class'] = calculator.__class__.__name__

    return calc_dict


def _make_results_dict(atoms):
    '''
    Create a dictionary from an ase.Atoms' object's `calculator` attribute

    Arg:
        atoms   ase.Atoms object
    Returns:
        results_dict    A dictionary with various calculator information stored.
                        Returns an empty dictionary if there is no calculator.
    '''
    results_dict = OrderedDict()
    calculator = atoms.get_calculator()

    # Results. This may duplicate information in the calculator,
    # but we have no control on what the calculator does.
    if calculator:

        if not calculator.calculation_required(atoms, ['energy']):
            results_dict['energy'] = atoms.get_potential_energy(apply_constraint=False)

        if not calculator.calculation_required(atoms, ['forces']):
            forces = atoms.get_forces(apply_constraint=False)
            results_dict['forces'] = forces.tolist()

            # fmax will be the max force component w/ constraints applied
            results_dict['fmax'] = max(np.abs(atoms.get_forces().flatten()))

    return results_dict


def make_atoms_from_doc(doc):
    '''
    This is the inversion function for `make_doc_from_atoms`; it takes
    Mongo documents created by that function and turns them back into
    an ase.Atoms object.

    Args:
        doc     Dictionary/json/Mongo document created by the
                `make_doc_from_atoms` function.
    Returns:
        atoms   ase.Atoms object with an ase.SinglePointCalculator attached
    '''
    atoms = Atoms([Atom(atom['symbol'],
                        atom['position'],
                        tag=atom['tag'],
                        momentum=atom['momentum'],
                        magmom=atom['magmom'],
                        charge=atom['charge'])
                   for atom in doc['atoms']['atoms']],
                  cell=doc['atoms']['cell']['array'],
                  pbc=doc['atoms']['pbc'],
                  info=doc['atoms']['info'],
                  constraint=[dict2constraint(constraint_dict)
                              for constraint_dict in doc['atoms']['constraints']])
    results = doc['results']
    calc = SinglePointCalculator(energy=results.get('energy', None),
                                 forces=results.get('forces', None),
                                 stress=results.get('stress', None),
                                 atoms=atoms)
    atoms.set_calculator(calc)
    return atoms
